from __future__ import annotations

import asyncio
import csv
import io
import math
import json
import os
import random
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

DATABASE = os.getenv("DATABASE_PATH", "data/kai_bot.db")
EMBED_COLOR = discord.Color.from_str("#7D3CFF")
CUP_NAME = "KAI CUP 2026"



class BrandedEmbed(discord.Embed):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        super().set_footer(text="Kai-Bot")
    def set_footer(self,**kwargs):
        return super().set_footer(text=(str(kwargs.get("text", "")).replace("Kai-Bot", "").strip() + "\nKai-Bot").strip())

def branded_text(text):
    return str(text) + "\n-# Kai-Bot"

class RosterIntegrityError(ValueError):
    def __init__(self, phase: str, expected: int, issues: list[dict]):
        self.phase = phase
        self.expected = expected
        self.issues = issues
        details = ", ".join(
            f"Team {row['team_number']} : {row['actual']}/{expected}"
            + (f" ({row['status']})" if row.get("status") != "active" else "")
            for row in issues
        )
        super().__init__(
            f"Rosters invalides pour {phase} — effectif attendu : {expected}. {details}"
        )


class SeedingIntegrityError(ValueError):
    """Le classement ne peut pas être calculé sans inventer de données ou de départage."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tag(tag: str) -> str:
    tag = (tag or "").strip().upper().replace("O", "0")
    if not tag:
        return ""
    if not tag.startswith("#"):
        tag = f"#{tag}"
    return tag


def encode_tag(tag: str) -> str:
    return quote(normalize_tag(tag), safe="")


def bool_text(value: bool) -> str:
    return "Oui" if value else "Non"


class CupRegistrationModal(discord.ui.Modal, title="Inscription KAI CUP 2026"):
    player_tag = discord.ui.TextInput(
        label="Tag Clash of Clans",
        placeholder="#ABC123XYZ",
        min_length=4,
        max_length=15,
        required=True,
    )

    def __init__(self, cog: "KaiCup", declared_kai_member: bool):
        super().__init__(timeout=180)
        self.cog = cog
        self.declared_kai_member = declared_kai_member

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.register_player(
            interaction=interaction,
            raw_tag=str(self.player_tag),
            declared_kai_member=self.declared_kai_member,
        )


class CupMemberChoiceView(discord.ui.View):
    """Mini-sondage demandé : membre du clan Kai ou externe."""

    def __init__(self, cog: "KaiCup"):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(
        label="Oui, je suis du clan Kai",
        style=discord.ButtonStyle.primary,
    )
    async def kai_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            CupRegistrationModal(self.cog, declared_kai_member=True)
        )

    @discord.ui.button(
        label="Non, je suis externe",
        style=discord.ButtonStyle.secondary,
    )
    async def external_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            CupRegistrationModal(self.cog, declared_kai_member=False)
        )


class CupRegistrationPanel(discord.ui.View):
    """Vue persistante : survit aux redémarrages du bot."""

    def __init__(self, cog: "KaiCup"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="S'inscrire",
        style=discord.ButtonStyle.success,
        custom_id="kai_cup_register_v1",
    )
    async def register(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("❌ L'inscription doit être faite depuis le serveur."),
                ephemeral=True,
            )

        config = await self.cog.get_config(interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                branded_text("❌ La KAI CUP n'est pas encore configurée."),
                ephemeral=True,
            )

        if not config["registration_open"]:
            return await interaction.response.send_message(
                branded_text("🔒 Les inscriptions sont actuellement fermées."),
                ephemeral=True,
            )

        existing = await self.cog.get_registration_by_discord(
            interaction.guild.id, interaction.user.id
        )
        if existing:
            status = "inscrit" if existing["status"] == "active" else "sur liste d'attente"
            return await interaction.response.send_message(
                branded_text(f"ℹ️ Tu es déjà **{status}** avec "
                f"**{existing['player_name']}** `{existing['player_tag']}`.\n"
                "Utilise `/cup me` pour voir ton inscription ou `/cup unregister` pour l'annuler."),
                ephemeral=True,
            )

        embed = BrandedEmbed(
            title="🏆 Inscription KAI CUP",
            description=(
                "Avant de renseigner ton tag, indique simplement ta situation.\n\n"
                "Le bot **vérifiera ensuite automatiquement** ton HDV et ton clan "
                "via l'API Clash of Clans."
            ),
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Question",
            value="**Es-tu actuellement membre du clan Kai ?**",
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            view=CupMemberChoiceView(self.cog),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Mon inscription",
        style=discord.ButtonStyle.secondary,
        custom_id="kai_cup_my_registration_v1",
    )
    async def my_registration(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.cog.send_my_registration(interaction)

    @discord.ui.button(
        label="Règlement",
        style=discord.ButtonStyle.secondary,
        custom_id="kai_cup_rules_v1",
    )
    async def rules(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        embed = BrandedEmbed(
            title="📜 KAI CUP • Règlement",
            description=(
                "Le règlement complet est disponible dans le salon prévu par le staff.\n\n"
                "**Rappels essentiels :**\n"
                "• HDV18 uniquement\n"
                "• 1 seul compte par participant\n"
                "• 72 places maximum\n"
                "• 60 places prioritaires Kai + 12 externes\n"
                "• Compétition le 11 octobre 2026\n"
                "• Présence obligatoire à 19h00"
            ),
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)



class CupDraftSelect(discord.ui.Select):
    def __init__(self, cog: "KaiCup", match_id: int, candidates: list[dict]):
        self.cog = cog
        self.match_id = int(match_id)

        options = [
            discord.SelectOption(
                label=row["player_name"][:100],
                value=row["player_tag"],
                description=f"{row['player_tag']} • joueur éliminé",
            )
            for row in candidates[:25]
        ]
        super().__init__(
            placeholder="Choisis le joueur à recruter",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"kai_cup_draft_{match_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("Action disponible uniquement sur le serveur."),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.execute_draft(
            guild=interaction.guild,
            match_id=self.match_id,
            player_tag=self.values[0],
            actor_id=interaction.user.id,
            interaction=interaction,
        )


class CupDraftView(discord.ui.View):
    def __init__(self, cog: "KaiCup", match_id: int, candidates: list[dict]):
        super().__init__(timeout=300)
        self.add_item(CupDraftSelect(cog, match_id, candidates))


class KaiCup(
    commands.GroupCog,
    group_name="cup",
    group_description="Gestion de la KAI CUP",
):
    """KAI CUP FINAL — inscriptions, Teams, Clan Cup, matchs, live, drafts et playoffs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_token = os.getenv("COC_API_TOKEN")
        self.session: Optional[aiohttp.ClientSession] = None
        self.registration_lock = asyncio.Lock()
        self.draw_lock = asyncio.Lock()
        self.draft_lock = asyncio.Lock()
        self.match_lock = asyncio.Lock()
        self.advance_lock = asyncio.Lock()
        self.finalization_lock = asyncio.Lock()

    async def cog_load(self):
        await self.setup_database()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.bot.add_view(CupRegistrationPanel(self))
        if not getattr(self.bot, "offline", False) and not self.cup_live_loop.is_running():
            self.cup_live_loop.start()

    def cog_unload(self):
        self.cup_live_loop.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ---------------------------------------------------------------------
    # BASE DE DONNÉES
    # ---------------------------------------------------------------------

    async def setup_database(self):
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_config (
                    guild_id TEXT PRIMARY KEY,
                    cup_name TEXT NOT NULL DEFAULT 'KAI CUP 2026',
                    kai_clan_tag TEXT,
                    panel_channel_id TEXT,
                    panel_message_id TEXT,
                    participant_role_id TEXT,
                    registration_open INTEGER NOT NULL DEFAULT 0,
                    internal_limit INTEGER NOT NULL DEFAULT 60,
                    external_limit INTEGER NOT NULL DEFAULT 12,
                    updated_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    discord_id TEXT NOT NULL,
                    discord_name TEXT,
                    player_tag TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    townhall_level INTEGER NOT NULL,
                    clan_tag TEXT,
                    clan_name TEXT,
                    declared_kai_member INTEGER NOT NULL DEFAULT 0,
                    actual_kai_member INTEGER NOT NULL DEFAULT 0,
                    participant_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    waitlist_position INTEGER,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(guild_id, discord_id),
                    UNIQUE(guild_id, player_tag)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    actor_discord_id TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_reg_status
                ON cup_registrations(guild_id, status, participant_type)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_draws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_code TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    test_mode INTEGER NOT NULL DEFAULT 0,
                    participant_count INTEGER NOT NULL,
                    team_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    reset_by TEXT,
                    reset_at TEXT,
                    UNIQUE(guild_id, draw_code)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    team_name TEXT NOT NULL,
                    captain_discord_id TEXT,
                    role_id TEXT,
                    channel_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    UNIQUE(guild_id, draw_id, team_number)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_draw_plans (
                    draw_id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    announcement_channel_id TEXT,
                    reveal_delay INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'preparing',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_draw_team_steps (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    roster_message_id TEXT,
                    welcome_message_id TEXT,
                    public_message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, team_number)
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_draw_team_steps_pending
                ON cup_draw_team_steps(guild_id, draw_id, status)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_team_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    discord_id TEXT NOT NULL,
                    player_tag TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    member_order INTEGER NOT NULL DEFAULT 1,
                    is_captain INTEGER NOT NULL DEFAULT 0,
                    joined_at TEXT NOT NULL,
                    UNIQUE(guild_id, draw_id, discord_id)
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_team_members_lookup
                ON cup_team_members(guild_id, draw_id, discord_id)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_event_config (
                    guild_id TEXT PRIMARY KEY,
                    event_category_id TEXT,
                    matches_category_id TEXT,
                    staff_category_id TEXT,
                    live_channel_id TEXT,
                    results_channel_id TEXT,
                    bracket_channel_id TEXT,
                    control_channel_id TEXT,
                    logs_channel_id TEXT,
                    updated_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_team_clans (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    clan_tag TEXT NOT NULL,
                    clan_name TEXT NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 1,
                    set_by TEXT,
                    set_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, team_number),
                    UNIQUE(guild_id, draw_id, clan_tag)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_checkins (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    discord_id TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, discord_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_state (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    current_phase TEXT,
                    status TEXT NOT NULL DEFAULT 'ready',
                    champion_team INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    match_order INTEGER NOT NULL,
                    team_a INTEGER NOT NULL,
                    team_b INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting_clan',
                    team_a_clan_tag TEXT,
                    team_b_clan_tag TEXT,
                    war_key TEXT,
                    team_a_stars INTEGER NOT NULL DEFAULT 0,
                    team_b_stars INTEGER NOT NULL DEFAULT 0,
                    team_a_destruction REAL NOT NULL DEFAULT 0,
                    team_b_destruction REAL NOT NULL DEFAULT 0,
                    team_a_duration REAL NOT NULL DEFAULT 0,
                    team_b_duration REAL NOT NULL DEFAULT 0,
                    winner_team INTEGER,
                    loser_team INTEGER,
                    match_channel_id TEXT,
                    match_message_id TEXT,
                    live_message_id TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(guild_id, draw_id, phase, match_order)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_lineups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    match_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    discord_id TEXT NOT NULL,
                    player_tag TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    is_substitute INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(match_id, team_number, player_tag)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_attacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    match_id INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    team_number INTEGER NOT NULL,
                    attacker_tag TEXT NOT NULL,
                    attacker_name TEXT,
                    defender_tag TEXT NOT NULL,
                    defender_name TEXT,
                    attack_order INTEGER NOT NULL DEFAULT 0,
                    stars INTEGER NOT NULL DEFAULT 0,
                    destruction REAL NOT NULL DEFAULT 0,
                    duration REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(match_id, attacker_tag, defender_tag, attack_order)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_drafts (
                    match_id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    winner_team INTEGER NOT NULL,
                    loser_team INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    selected_player_tag TEXT,
                    deadline TEXT NOT NULL,
                    message_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_penalties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    match_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    stars_adjustment INTEGER NOT NULL DEFAULT 0,
                    destruction_adjustment REAL NOT NULL DEFAULT 0,
                    reason TEXT,
                    actor_discord_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_seeds (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    team_number INTEGER NOT NULL,
                    seed INTEGER NOT NULL,
                    stars INTEGER NOT NULL DEFAULT 0,
                    destruction REAL NOT NULL DEFAULT 0,
                    avg_duration REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, team_number),
                    UNIQUE(guild_id, draw_id, seed)
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_matches_state
                ON cup_matches(guild_id, draw_id, status, phase)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_attacks_match
                ON cup_attacks(match_id, team_number)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_phase_plans (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    pairs_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'preparing',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, phase)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_match_finalizations (
                    match_id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    result_announced INTEGER NOT NULL DEFAULT 0,
                    progression_done INTEGER NOT NULL DEFAULT 0,
                    champion_done INTEGER NOT NULL DEFAULT 0,
                    bracket_done INTEGER NOT NULL DEFAULT 0,
                    channel_cleanup_done INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cup_finalizations_pending
                ON cup_match_finalizations(guild_id, draw_id, status)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cup_public_messages (
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, kind)
                )
            """)

            await db.commit()

    async def audit(
        self,
        guild_id: int,
        actor_id: Optional[int],
        action: str,
        details: str = "",
    ):
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                INSERT INTO cup_audit_logs(
                    guild_id,actor_discord_id,action,details,created_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    str(guild_id),
                    str(actor_id) if actor_id else None,
                    action,
                    details,
                    utc_now(),
                ),
            )
            await db.commit()

    async def get_config(self, guild_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_config WHERE guild_id=?",
                (str(guild_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_counts(self, guild_id: int) -> dict:
        counts = {
            "kai_active": 0,
            "external_active": 0,
            "kai_waiting": 0,
            "external_waiting": 0,
        }
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT participant_type,status,COUNT(*)
                FROM cup_registrations
                WHERE guild_id=?
                GROUP BY participant_type,status
                """,
                (str(guild_id),),
            ) as cur:
                for ptype, status, count in await cur.fetchall():
                    key = f"{ptype}_{'active' if status == 'active' else 'waiting'}"
                    if key in counts:
                        counts[key] = int(count)
        counts["total_active"] = counts["kai_active"] + counts["external_active"]
        counts["total_waiting"] = counts["kai_waiting"] + counts["external_waiting"]
        return counts

    async def get_registration_by_discord(
        self,
        guild_id: int,
        discord_id: int,
    ) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM cup_registrations
                WHERE guild_id=? AND discord_id=?
                """,
                (str(guild_id), str(discord_id)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def next_waitlist_position(self, guild_id: int, participant_type: str) -> int:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT COALESCE(MAX(waitlist_position),0)+1
                FROM cup_registrations
                WHERE guild_id=? AND participant_type=? AND status='waiting'
                """,
                (str(guild_id), participant_type),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0] or 1)

    # ---------------------------------------------------------------------
    # API CLASH
    # ---------------------------------------------------------------------

    async def api_get(self, endpoint: str) -> Optional[dict]:
        if not self.api_token:
            return None

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

        url = f"https://api.clashofclans.com/v1{endpoint}"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        try:
            async with self.session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status != 200:
                    return None
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def fetch_player(self, tag: str) -> Optional[dict]:
        return await self.api_get(f"/players/{encode_tag(tag)}")

    # ---------------------------------------------------------------------
    # PANNEAU
    # ---------------------------------------------------------------------

    def build_registration_embed(self, config: dict, counts: dict) -> discord.Embed:
        is_open = bool(config["registration_open"])
        state = "🟢 OUVERTES" if is_open else "🔒 FERMÉES"

        internal_limit = int(config["internal_limit"])
        external_limit = int(config["external_limit"])
        total_limit = internal_limit + external_limit

        embed = BrandedEmbed(
            title="🏆 KAI CUP 2026 • INSCRIPTIONS",
            description=(
                "**Une soirée. 72 joueurs. 24 Teams. Un champion.**\n\n"
                "📅 **Dimanche 11 octobre 2026**\n"
                "🕢 **19h30 → ~23h00**\n"
                "🏰 **HDV18 uniquement**\n\n"
                f"État des inscriptions : **{state}**"
            ),
            color=EMBED_COLOR if is_open else discord.Color.dark_grey(),
        )

        embed.add_field(
            name="🟣 Clan Kai",
            value=f"**{counts['kai_active']}/{internal_limit}** places",
            inline=True,
        )
        embed.add_field(
            name="🌍 Externes",
            value=f"**{counts['external_active']}/{external_limit}** places",
            inline=True,
        )
        embed.add_field(
            name="👥 Total",
            value=f"**{counts['total_active']}/{total_limit}**",
            inline=True,
        )

        if counts["total_waiting"]:
            embed.add_field(
                name="⏳ Liste d'attente",
                value=(
                    f"Kai : **{counts['kai_waiting']}** • "
                    f"Externes : **{counts['external_waiting']}**"
                ),
                inline=False,
            )

        embed.add_field(
            name="Comment s'inscrire ?",
            value=(
                "1. Clique sur **S'inscrire**.\n"
                "2. Indique si tu es membre du clan Kai.\n"
                "3. Entre ton **tag Clash**.\n"
                "4. Le bot vérifie automatiquement ton **HDV18** et ton **clan**."
            ),
            inline=False,
        )

        embed.add_field(
            name="Important",
            value=(
                "Les 60 places Kai sont prioritaires pendant les inscriptions. "
                "À la fermeture, les places Kai non utilisées peuvent être "
                "attribuées aux joueurs externes en attente."
            ),
            inline=False,
        )

        kai_tag = config.get("kai_clan_tag") or "Non configuré"
        embed.set_footer(text=f"KAI CUP • Clan Kai {kai_tag} • Bot automatique")
        return embed

    async def update_panel(self, guild_id: int):
        config = await self.get_config(guild_id)
        if not config:
            return

        channel_id = config.get("panel_channel_id")
        message_id = config.get("panel_message_id")
        if not channel_id or not message_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        counts = await self.get_counts(guild_id)
        await message.edit(
            embed=self.build_registration_embed(config, counts),
            view=CupRegistrationPanel(self),
        )

    # ---------------------------------------------------------------------
    # INSCRIPTION
    # ---------------------------------------------------------------------

    async def register_player(
        self,
        interaction: discord.Interaction,
        raw_tag: str,
        declared_kai_member: bool,
    ):
        if not interaction.guild:
            return await interaction.followup.send(
                branded_text("❌ Cette action doit être faite sur le serveur."),
                ephemeral=True,
            )

        async with self.registration_lock:
            if await self.registrations_frozen(interaction):
                return
            guild_id = interaction.guild.id
            config = await self.get_config(guild_id)

            if not config:
                return await interaction.followup.send(
                    branded_text("❌ La KAI CUP n'est pas encore configurée."),
                    ephemeral=True,
                )

            if not config["registration_open"]:
                return await interaction.followup.send(
                    branded_text("🔒 Les inscriptions sont fermées."),
                    ephemeral=True,
                )

            if await self.get_registration_by_discord(guild_id, interaction.user.id):
                return await interaction.followup.send(
                    branded_text("❌ Tu possèdes déjà une inscription."),
                    ephemeral=True,
                )

            tag = normalize_tag(raw_tag)
            player = await self.fetch_player(tag)

            if not player:
                return await interaction.followup.send(
                    branded_text("❌ Impossible de trouver ce compte via l'API Clash of Clans.\n"
                    "Vérifie ton tag puis réessaie."),
                    ephemeral=True,
                )

            townhall = int(player.get("townHallLevel", 0) or 0)
            if townhall != 18:
                return await interaction.followup.send(
                    branded_text(f"❌ La KAI CUP est réservée aux **HDV18**.\n"
                    f"Compte détecté : **{player.get('name', 'Inconnu')} • HDV{townhall}**."),
                    ephemeral=True,
                )

            kai_clan_tag = normalize_tag(config.get("kai_clan_tag") or "")
            clan = player.get("clan") or {}
            player_clan_tag = normalize_tag(clan.get("tag") or "")
            player_clan_name = clan.get("name") or "Sans clan"

            actual_kai_member = bool(
                kai_clan_tag and player_clan_tag and player_clan_tag == kai_clan_tag
            )

            # La classification officielle est celle vérifiée par l'API.
            participant_type = "kai" if actual_kai_member else "external"

            counts = await self.get_counts(guild_id)
            internal_limit = int(config["internal_limit"])
            external_limit = int(config["external_limit"])

            if participant_type == "kai":
                has_slot = counts["kai_active"] < internal_limit
            else:
                has_slot = counts["external_active"] < external_limit

            status = "active" if has_slot else "waiting"
            wait_position = None
            if status == "waiting":
                wait_position = await self.next_waitlist_position(
                    guild_id, participant_type
                )

            try:
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        """
                        INSERT INTO cup_registrations(
                            guild_id,discord_id,discord_name,
                            player_tag,player_name,townhall_level,
                            clan_tag,clan_name,
                            declared_kai_member,actual_kai_member,
                            participant_type,status,waitlist_position,
                            registered_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            str(guild_id),
                            str(interaction.user.id),
                            str(interaction.user),
                            normalize_tag(player.get("tag") or tag),
                            player.get("name") or "Inconnu",
                            townhall,
                            player_clan_tag or None,
                            player_clan_name,
                            int(declared_kai_member),
                            int(actual_kai_member),
                            participant_type,
                            status,
                            wait_position,
                            utc_now(),
                            utc_now(),
                        ),
                    )
                    await db.commit()
            except aiosqlite.IntegrityError:
                return await interaction.followup.send(
                    branded_text("❌ Ce compte Clash ou ce membre Discord est déjà inscrit."),
                    ephemeral=True,
                )

            mismatch_note = ""
            if declared_kai_member != actual_kai_member:
                if actual_kai_member:
                    mismatch_note = (
                        "\n\nℹ️ Tu avais indiqué **externe**, mais l'API te détecte "
                        "dans le clan Kai : tu as donc été classé **Kai**."
                    )
                else:
                    mismatch_note = (
                        "\n\nℹ️ Tu avais indiqué **membre Kai**, mais l'API ne détecte "
                        "pas ce compte dans le clan Kai configuré : tu as donc été "
                        "classé **externe**."
                    )

            if status == "active":
                role = await self.get_participant_role(interaction.guild, config)
                if role and isinstance(interaction.user, discord.Member):
                    try:
                        await interaction.user.add_roles(
                            role,
                            reason="Inscription KAI CUP validée",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                title = "✅ INSCRIPTION VALIDÉE"
                description = (
                    f"Bienvenue dans la **{config['cup_name']}** !\n\n"
                    f"**Joueur :** {player.get('name')} `{tag}`\n"
                    f"**HDV :** {townhall}\n"
                    f"**Clan :** {player_clan_name}\n"
                    f"**Catégorie :** {'🟣 Clan Kai' if participant_type == 'kai' else '🌍 Externe'}"
                    f"{mismatch_note}"
                )
                color = discord.Color.green()
            else:
                title = "⏳ LISTE D'ATTENTE"
                description = (
                    f"Ton inscription est enregistrée, mais le quota "
                    f"{'Kai' if participant_type == 'kai' else 'externe'} est actuellement complet.\n\n"
                    f"**Position d'attente : #{wait_position}**\n"
                    f"**Joueur :** {player.get('name')} `{tag}`\n"
                    f"**HDV :** {townhall}\n"
                    f"**Clan :** {player_clan_name}"
                    f"{mismatch_note}\n\n"
                    "Si une place se libère — ou si des places Kai sont redistribuées "
                    "aux externes à la fermeture — le bot peut te promouvoir automatiquement."
                )
                color = discord.Color.orange()

            await self.audit(
                guild_id,
                interaction.user.id,
                "registration_created",
                (
                    f"{player.get('name')} {tag} type={participant_type} "
                    f"status={status} declared_kai={declared_kai_member} "
                    f"actual_kai={actual_kai_member}"
                ),
            )

            await interaction.followup.send(
                embed=BrandedEmbed(
                    title=title,
                    description=description,
                    color=color,
                ),
                ephemeral=True,
            )

            await self.update_panel(guild_id)

    async def get_participant_role(
        self,
        guild: discord.Guild,
        config: dict,
    ) -> Optional[discord.Role]:
        role_id = config.get("participant_role_id")
        if not role_id:
            return None
        return guild.get_role(int(role_id))

    async def send_my_registration(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("❌ Commande disponible uniquement sur le serveur."),
                ephemeral=True,
            )

        row = await self.get_registration_by_discord(
            interaction.guild.id, interaction.user.id
        )
        if not row:
            return await interaction.response.send_message(
                branded_text("❌ Tu n'es pas encore inscrit à la KAI CUP."),
                ephemeral=True,
            )

        if row["status"] == "active":
            status = "✅ Inscription validée"
            color = discord.Color.green()
        else:
            status = f"⏳ Liste d'attente #{row['waitlist_position'] or '?'}"
            color = discord.Color.orange()

        embed = BrandedEmbed(
            title="🏆 Mon inscription KAI CUP",
            description=status,
            color=color,
        )
        embed.add_field(
            name="Joueur",
            value=f"**{row['player_name']}** `{row['player_tag']}`",
            inline=False,
        )
        embed.add_field(
            name="HDV",
            value=f"**HDV{row['townhall_level']}**",
            inline=True,
        )
        embed.add_field(
            name="Catégorie",
            value="🟣 Kai" if row["participant_type"] == "kai" else "🌍 Externe",
            inline=True,
        )
        embed.add_field(
            name="Clan détecté",
            value=f"**{row['clan_name'] or 'Sans clan'}** `{row['clan_tag'] or '—'}`",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def promote_waitlist(
        self,
        guild: discord.Guild,
        participant_type: str,
        number: int = 1,
    ) -> list[int]:
        """Promouvoir les N premiers joueurs d'une liste d'attente."""
        if number <= 0:
            return []

        promoted_ids: list[int] = []
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_registrations
                WHERE guild_id=? AND participant_type=? AND status='waiting'
                ORDER BY waitlist_position ASC, registered_at ASC
                LIMIT ?
                """,
                (str(guild.id), participant_type, int(number)),
            ) as cur:
                rows = await cur.fetchall()

            for row in rows:
                await db.execute(
                    """
                    UPDATE cup_registrations
                    SET status='active',waitlist_position=NULL,updated_at=?
                    WHERE id=?
                    """,
                    (utc_now(), row["id"]),
                )
                promoted_ids.append(int(row["discord_id"]))

            # Re-numérote proprement la liste restante.
            async with db.execute(
                """
                SELECT id
                FROM cup_registrations
                WHERE guild_id=? AND participant_type=? AND status='waiting'
                ORDER BY waitlist_position ASC, registered_at ASC
                """,
                (str(guild.id), participant_type),
            ) as cur:
                remaining = await cur.fetchall()

            for position, row in enumerate(remaining, start=1):
                await db.execute(
                    "UPDATE cup_registrations SET waitlist_position=? WHERE id=?",
                    (position, row["id"]),
                )

            await db.commit()

        config = await self.get_config(guild.id)
        role = await self.get_participant_role(guild, config or {})

        for discord_id in promoted_ids:
            member = guild.get_member(discord_id)
            if member and role:
                try:
                    await member.add_roles(
                        role,
                        reason="Promotion liste d'attente KAI CUP",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if member:
                try:
                    await member.send(
                        branded_text("🏆 **KAI CUP 2026**\n"
                        "Une place vient de se libérer : **ton inscription est maintenant validée !**")
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        return promoted_ids


    # ---------------------------------------------------------------------
    # TIRAGE & TEAMS
    # ---------------------------------------------------------------------

    async def registration_draw(self, guild_id: int) -> Optional[dict]:
        """An interrupted draw still freezes its participant snapshot."""
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_draws WHERE guild_id=? "
                "AND status IN ('preparing','active','error') ORDER BY id DESC LIMIT 1",
                (str(guild_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def registrations_frozen(self, interaction: discord.Interaction) -> bool:
        if await self.registration_draw(interaction.guild.id):
            await interaction.followup.send(
                branded_text("🔒 Un tirage existe : inscriptions, désinscriptions et quotas sont verrouillés. "
                "Contacte le staff pour une absence ou un remplacement."),
                ephemeral=True,
            )
            return True
        return False

    async def get_active_draw(self, guild_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_draws
                WHERE guild_id=? AND status='active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(guild_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_active_participants(self, guild_id: int) -> list[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT discord_id,discord_name,player_tag,player_name,
                       participant_type,registered_at
                FROM cup_registrations
                WHERE guild_id=? AND status='active'
                ORDER BY registered_at ASC
                """,
                (str(guild_id),),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_team_for_member(
        self,
        guild_id: int,
        discord_id: int,
    ) -> Optional[dict]:
        draw = await self.get_active_draw(guild_id)
        if not draw:
            return None

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT tm.team_number,t.team_name,t.captain_discord_id,
                       t.role_id,t.channel_id,t.draw_id
                FROM cup_team_members tm
                JOIN cup_teams t
                  ON t.guild_id=tm.guild_id
                 AND t.draw_id=tm.draw_id
                 AND t.team_number=tm.team_number
                WHERE tm.guild_id=? AND tm.draw_id=? AND tm.discord_id=?
                  AND t.status='active'
                LIMIT 1
                """,
                (str(guild_id), int(draw["id"]), str(discord_id)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_team_members(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
    ) -> list[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT discord_id,player_tag,player_name,member_order,is_captain
                FROM cup_team_members
                WHERE guild_id=? AND draw_id=? AND team_number=?
                ORDER BY member_order ASC
                """,
                (str(guild_id), int(draw_id), int(team_number)),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    def build_team_embed(
        self,
        guild: discord.Guild,
        team: dict,
        members: list[dict],
    ) -> discord.Embed:
        captain_id = str(team.get("captain_discord_id") or "")
        lines = []
        for index, row in enumerate(members, start=1):
            discord_id = int(row["discord_id"])
            member = guild.get_member(discord_id)
            mention = member.mention if member else f"<@{discord_id}>"
            crown = " [CAPITAINE]" if str(discord_id) == captain_id else ""
            lines.append(
                f"**{index}.** {mention} — **{row['player_name']}** "
                f"`{row['player_tag']}`{crown}"
            )

        embed = BrandedEmbed(
            title=f"🏆 {team['team_name']}",
            description="\n".join(lines) if lines else "Aucun joueur.",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Capitaine",
            value=f"<@{captain_id}>" if captain_id else "Non défini",
            inline=True,
        )
        if team.get("channel_id"):
            embed.add_field(
                name="QG privé",
                value=f"<#{team['channel_id']}>",
                inline=True,
            )
        embed.set_footer(text="KAI CUP 2026 • Team officielle")
        return embed

    async def ensure_teams_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        category = discord.utils.get(guild.categories, name="👥 KAI CUP — TEAMS")
        if category:
            return category

        bot_member = guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        return await guild.create_category(
            "👥 KAI CUP — TEAMS",
            overwrites=overwrites,
            reason="KAI CUP • création des QG privés",
        )

    async def create_team_discord_space(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        team_number: int,
        members: list[dict],
    ) -> tuple[discord.Role, discord.TextChannel]:
        role_name = f"KAI CUP • Team {team_number:02d}"
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            role = await guild.create_role(
                name=role_name,
                colour=EMBED_COLOR,
                mentionable=False,
                hoist=False,
                reason=f"KAI CUP • Team {team_number}",
            )

        for row in members:
            member = guild.get_member(int(row["discord_id"]))
            if member:
                try:
                    await member.add_roles(
                        role,
                        reason=f"KAI CUP • affectation Team {team_number}",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    raise

        channel_name = f"team-{team_number:02d}"
        existing = discord.utils.get(category.text_channels, name=channel_name)
        if existing:
            channel = existing
            await channel.set_permissions(
                role,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        else:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                ),
            }
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                )

            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"KAI CUP 2026 • QG privé Team {team_number}",
                reason=f"KAI CUP • création QG Team {team_number}",
            )

        return role, channel

    async def announce_team(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        team_number: int,
        members: list[dict],
        captain_id: int,
    ):
        lines = []
        for index, row in enumerate(members, start=1):
            discord_id = int(row["discord_id"])
            suffix = " — **CAPITAINE**" if discord_id == captain_id else ""
            lines.append(
                f"**{index}.** <@{discord_id}> • **{row['player_name']}** "
                f"`{row['player_tag']}`{suffix}"
            )

        embed = BrandedEmbed(
            title=f"🎲 TEAM {team_number}",
            description="\n".join(lines),
            color=EMBED_COLOR,
        )
        embed.set_footer(text="KAI CUP 2026 • Tirage officiel")
        return await channel.send(embed=embed)

    async def get_draw_plan(self, draw_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_draw_plans WHERE draw_id=?",
                (int(draw_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_draw_team_step(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
    ) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM cup_draw_team_steps
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (str(guild_id), int(draw_id), int(team_number)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def saved_message_exists(
        self,
        channel: discord.TextChannel,
        message_id: Optional[str],
    ) -> bool:
        if not message_id:
            return False
        try:
            await channel.fetch_message(int(message_id))
            return True
        except discord.NotFound:
            return False

    async def save_draw_team_step(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
        **changes,
    ):
        allowed = {
            "roster_message_id",
            "welcome_message_id",
            "public_message_id",
            "status",
            "last_error",
        }
        if not changes or not set(changes).issubset(allowed):
            raise ValueError("Étape de tirage invalide")
        assignments = ",".join(f"{column}=?" for column in changes)
        values = list(changes.values())
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                f"""
                UPDATE cup_draw_team_steps
                SET {assignments},updated_at=?
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (
                    *values,
                    utc_now(),
                    str(guild_id),
                    int(draw_id),
                    int(team_number),
                ),
            )
            await db.commit()

    async def resume_draw_creation(
        self,
        guild: discord.Guild,
        draw_id: int,
    ) -> bool:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_draws WHERE id=? AND guild_id=?",
                (int(draw_id), str(guild.id)),
            ) as cur:
                draw_row = await cur.fetchone()
            if not draw_row:
                return False
            draw = dict(draw_row)
            plan = await self.get_draw_plan(draw_id)
            if draw["status"] == "active" and plan and plan["status"] == "ready":
                return False
            now = utc_now()
            if not plan:
                raise RuntimeError(
                    "Tirage antérieur sans journal de reprise ; intervention staff requise"
                )
            await db.execute(
                "UPDATE cup_draws SET status='preparing' WHERE id=?",
                (int(draw_id),),
            )
            await db.execute(
                """
                UPDATE cup_draw_plans
                SET status='preparing',last_error=NULL,updated_at=?
                WHERE draw_id=?
                """,
                (now, int(draw_id)),
            )
            async with db.execute(
                """
                SELECT * FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND status='active'
                ORDER BY team_number
                """,
                (str(guild.id), int(draw_id)),
            ) as cur:
                teams = [dict(row) for row in await cur.fetchall()]
            for team in teams:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO cup_draw_team_steps(
                        guild_id,draw_id,team_number,status,updated_at
                    ) VALUES(?,?,?,'pending',?)
                    """,
                    (
                        str(guild.id),
                        int(draw_id),
                        int(team["team_number"]),
                        now,
                    ),
                )
            await db.commit()

        try:
            category = await self.ensure_teams_category(guild)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:1000]
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE cup_draws SET status='error' WHERE id=?",
                    (int(draw_id),),
                )
                await db.execute(
                    """
                    UPDATE cup_draw_plans
                    SET status='error',last_error=?,updated_at=?
                    WHERE draw_id=?
                    """,
                    (detail, utc_now(), int(draw_id)),
                )
                await db.commit()
            raise
        for team in teams:
            team_number = int(team["team_number"])
            step = await self.get_draw_team_step(guild.id, draw_id, team_number)
            if step and step["status"] == "ready":
                continue
            members = await self.get_team_members(guild.id, draw_id, team_number)
            try:
                role, channel = await self.create_team_discord_space(
                    guild,
                    category,
                    team_number,
                    members,
                )
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        """
                        UPDATE cup_teams SET role_id=?,channel_id=?
                        WHERE guild_id=? AND draw_id=? AND team_number=?
                        """,
                        (
                            str(role.id),
                            str(channel.id),
                            str(guild.id),
                            int(draw_id),
                            team_number,
                        ),
                    )
                    await db.commit()

                step = await self.get_draw_team_step(guild.id, draw_id, team_number)
                captain_id = int(team["captain_discord_id"])
                if not await self.saved_message_exists(
                    channel,
                    step.get("roster_message_id") if step else None,
                ):
                    team_row = {
                        "team_name": team["team_name"],
                        "captain_discord_id": str(captain_id),
                        "channel_id": str(channel.id),
                    }
                    message = await channel.send(
                        content=branded_text(" ".join(f"<@{row['discord_id']}>" for row in members)),
                        embed=self.build_team_embed(guild, team_row, members),
                    )
                    await self.save_draw_team_step(
                        guild.id,
                        draw_id,
                        team_number,
                        roster_message_id=str(message.id),
                    )

                step = await self.get_draw_team_step(guild.id, draw_id, team_number)
                if not await self.saved_message_exists(
                    channel,
                    step.get("welcome_message_id") if step else None,
                ):
                    message = await channel.send(
                        branded_text("Bienvenue dans votre **QG KAI CUP**.\n"
                        "Le premier joueur tiré est capitaine provisoire. "
                        "Le staff peut le modifier avec `/cup set_captain`.\n"
                        "Gardez ce salon pour la stratégie, les disponibilités et le Clan Cup.")
                    )
                    await self.save_draw_team_step(
                        guild.id,
                        draw_id,
                        team_number,
                        welcome_message_id=str(message.id),
                    )

                announcement_channel_id = plan.get("announcement_channel_id")
                if announcement_channel_id:
                    public_channel = guild.get_channel(int(announcement_channel_id))
                    if not isinstance(public_channel, discord.TextChannel):
                        raise RuntimeError("Salon d'annonce du tirage introuvable")
                    step = await self.get_draw_team_step(guild.id, draw_id, team_number)
                    if not await self.saved_message_exists(
                        public_channel,
                        step.get("public_message_id") if step else None,
                    ):
                        message = await self.announce_team(
                            public_channel,
                            guild,
                            team_number,
                            members,
                            captain_id,
                        )
                        await self.save_draw_team_step(
                            guild.id,
                            draw_id,
                            team_number,
                            public_message_id=str(message.id),
                        )
                        if int(plan.get("reveal_delay") or 0):
                            await asyncio.sleep(int(plan["reveal_delay"]))

                await self.save_draw_team_step(
                    guild.id,
                    draw_id,
                    team_number,
                    status="ready",
                    last_error=None,
                )
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"[:1000]
                await self.save_draw_team_step(
                    guild.id,
                    draw_id,
                    team_number,
                    status="pending",
                    last_error=detail,
                )
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        "UPDATE cup_draws SET status='error' WHERE id=?",
                        (int(draw_id),),
                    )
                    await db.execute(
                        """
                        UPDATE cup_draw_plans
                        SET status='error',last_error=?,updated_at=?
                        WHERE draw_id=?
                        """,
                        (detail, utc_now(), int(draw_id)),
                    )
                    await db.commit()
                raise

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE cup_draws SET status='active' WHERE id=?",
                (int(draw_id),),
            )
            await db.execute(
                """
                UPDATE cup_draw_plans
                SET status='ready',last_error=NULL,updated_at=?
                WHERE draw_id=?
                """,
                (utc_now(), int(draw_id)),
            )
            await db.commit()
        return True



    # ---------------------------------------------------------------------
    # MOTEUR COMPLET KAI CUP
    # ---------------------------------------------------------------------

    async def get_event_config(self, guild_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_event_config WHERE guild_id=?",
                (str(guild_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_cup_state(self, guild_id: int, draw_id: int) -> dict:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_state WHERE guild_id=? AND draw_id=?",
                (str(guild_id), int(draw_id)),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)

            await db.execute(
                """
                INSERT OR IGNORE INTO cup_state(
                    guild_id,draw_id,current_phase,status,updated_at
                ) VALUES(?,?,?,?,?)
                """,
                (str(guild_id), int(draw_id), None, "ready", utc_now()),
            )
            await db.commit()

        return {
            "guild_id": str(guild_id),
            "draw_id": int(draw_id),
            "current_phase": None,
            "status": "ready",
            "champion_team": None,
        }

    async def set_cup_state(
        self,
        guild_id: int,
        draw_id: int,
        *,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        champion_team: Optional[int] = None,
    ):
        current = await self.get_cup_state(guild_id, draw_id)
        new_phase = phase if phase is not None else current.get("current_phase")
        new_status = status if status is not None else current.get("status", "ready")
        new_champion = (
            champion_team
            if champion_team is not None
            else current.get("champion_team")
        )
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                INSERT INTO cup_state(
                    guild_id,draw_id,current_phase,status,champion_team,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(guild_id,draw_id) DO UPDATE SET
                    current_phase=excluded.current_phase,
                    status=excluded.status,
                    champion_team=excluded.champion_team,
                    updated_at=excluded.updated_at
                """,
                (
                    str(guild_id),
                    int(draw_id),
                    new_phase,
                    new_status,
                    new_champion,
                    utc_now(),
                ),
            )
            await db.commit()

    async def ensure_event_space(self, guild: discord.Guild) -> dict:
        existing = await self.get_event_config(guild.id)
        if existing:
            ids = [
                existing.get("event_category_id"),
                existing.get("matches_category_id"),
                existing.get("staff_category_id"),
                existing.get("live_channel_id"),
                existing.get("results_channel_id"),
                existing.get("bracket_channel_id"),
            ]
            if all(not x or guild.get_channel(int(x)) for x in ids):
                return existing

        bot_member = guild.me

        public_category = discord.utils.get(guild.categories, name="🏆 KAI CUP")
        if not public_category:
            public_category = await guild.create_category(
                "🏆 KAI CUP",
                reason="KAI CUP • espace public",
            )

        matches_category = discord.utils.get(guild.categories, name="⚔️ KAI CUP — MATCHS")
        if not matches_category:
            ow = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if bot_member:
                ow[bot_member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    read_message_history=True,
                )
            matches_category = await guild.create_category(
                "⚔️ KAI CUP — MATCHS",
                overwrites=ow,
                reason="KAI CUP • matchs temporaires",
            )

        staff_category = discord.utils.get(guild.categories, name="🛠️ KAI CUP — STAFF")
        if not staff_category:
            ow = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if bot_member:
                ow[bot_member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    read_message_history=True,
                )
            staff_category = await guild.create_category(
                "🛠️ KAI CUP — STAFF",
                overwrites=ow,
                reason="KAI CUP • staff",
            )

        async def public_channel(name: str, topic: str) -> discord.TextChannel:
            channel = discord.utils.get(public_category.text_channels, name=name)
            if channel:
                return channel
            return await guild.create_text_channel(
                name,
                category=public_category,
                topic=topic,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=False,
                        read_message_history=True,
                    )
                },
                reason="KAI CUP • création salon public",
            )

        async def staff_channel(name: str, topic: str) -> discord.TextChannel:
            channel = discord.utils.get(staff_category.text_channels, name=name)
            if channel:
                return channel
            return await guild.create_text_channel(
                name,
                category=staff_category,
                topic=topic,
                reason="KAI CUP • création salon staff",
            )

        live = await public_channel("cup-live", "Scores et suivi live KAI CUP")
        results = await public_channel("cup-resultats", "Résultats officiels KAI CUP")
        bracket = await public_channel("cup-bracket", "Bracket KAI CUP")
        control = await staff_channel("cup-control", "Pilotage KAI CUP")
        logs = await staff_channel("cup-logs", "Logs techniques et audit KAI CUP")

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                INSERT INTO cup_event_config(
                    guild_id,event_category_id,matches_category_id,staff_category_id,
                    live_channel_id,results_channel_id,bracket_channel_id,
                    control_channel_id,logs_channel_id,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    event_category_id=excluded.event_category_id,
                    matches_category_id=excluded.matches_category_id,
                    staff_category_id=excluded.staff_category_id,
                    live_channel_id=excluded.live_channel_id,
                    results_channel_id=excluded.results_channel_id,
                    bracket_channel_id=excluded.bracket_channel_id,
                    control_channel_id=excluded.control_channel_id,
                    logs_channel_id=excluded.logs_channel_id,
                    updated_at=excluded.updated_at
                """,
                (
                    str(guild.id),
                    str(public_category.id),
                    str(matches_category.id),
                    str(staff_category.id),
                    str(live.id),
                    str(results.id),
                    str(bracket.id),
                    str(control.id),
                    str(logs.id),
                    utc_now(),
                ),
            )
            await db.commit()

        return await self.get_event_config(guild.id)

    async def cup_log(self, guild: discord.Guild, message: str):
        config = await self.get_event_config(guild.id)
        if not config or not config.get("logs_channel_id"):
            return
        channel = guild.get_channel(int(config["logs_channel_id"]))
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(branded_text(message[:1900]))
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def get_team_clan(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
    ) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_team_clans
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (str(guild_id), int(draw_id), int(team_number)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_team_row(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
    ) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (str(guild_id), int(draw_id), int(team_number)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def current_war(self, clan_tag: str) -> Optional[dict]:
        return await self.api_get(f"/clans/{encode_tag(clan_tag)}/currentwar")

    @staticmethod
    def cup_war_key(war: dict) -> str:
        return (
            f"{war.get('preparationStartTime', 'unknown')}_"
            f"{war.get('startTime', 'unknown')}_"
            f"{war.get('endTime', 'unknown')}"
        )

    @staticmethod
    def phase_label(phase: str) -> str:
        return {
            "round1": "ROUND 1",
            "round2": "ROUND 2",
            "playin": "PLAY-IN",
            "semifinal": "DEMI-FINALE",
            "final": "GRANDE FINALE",
        }.get(phase, phase.upper())

    @staticmethod
    def required_starters(phase: str) -> int:
        return 3 if phase in {"round1", "round2"} else 5

    @staticmethod
    def required_roster_size(phase: str) -> int:
        return {
            "round1": 3,
            "round2": 4,
            "playin": 5,
            "semifinal": 5,
            "final": 5,
        }[phase]

    async def roster_integrity_issues(
        self,
        guild_id: int,
        draw_id: int,
        team_numbers: list[int],
        expected: int,
    ) -> list[dict]:
        requested = sorted({int(team) for team in team_numbers})
        if not requested:
            return []
        placeholders = ",".join("?" for _ in requested)
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT t.team_number,t.status,COUNT(tm.id) AS member_count
                FROM cup_teams t
                LEFT JOIN cup_team_members tm
                  ON tm.guild_id=t.guild_id
                 AND tm.draw_id=t.draw_id
                 AND tm.team_number=t.team_number
                WHERE t.guild_id=? AND t.draw_id=?
                  AND t.team_number IN ({placeholders})
                GROUP BY t.team_number,t.status
                ORDER BY t.team_number
                """,
                (str(guild_id), int(draw_id), *requested),
            ) as cur:
                rows = {int(row["team_number"]): dict(row) for row in await cur.fetchall()}
        issues = []
        for team_number in requested:
            row = rows.get(team_number)
            actual = int(row["member_count"]) if row else 0
            status = str(row["status"]) if row else "introuvable"
            if actual != int(expected) or status != "active":
                issues.append(
                    {
                        "team_number": team_number,
                        "actual": actual,
                        "expected": int(expected),
                        "status": status,
                    }
                )
        return issues

    async def team_active_members(
        self,
        guild_id: int,
        draw_id: int,
        team_number: int,
    ) -> list[dict]:
        return await self.get_team_members(guild_id, draw_id, team_number)

    async def get_match(self, match_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_matches WHERE id=?",
                (int(match_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_phase_matches(
        self,
        guild_id: int,
        draw_id: int,
        phase: str,
    ) -> list[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_matches
                WHERE guild_id=? AND draw_id=? AND phase=?
                ORDER BY match_order ASC
                """,
                (str(guild_id), int(draw_id), phase),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_penalties(self, match_id: int) -> dict[int, tuple[int, float]]:
        result: dict[int, tuple[int, float]] = {}
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT team_number,
                       COALESCE(SUM(stars_adjustment),0),
                       COALESCE(SUM(destruction_adjustment),0)
                FROM cup_penalties
                WHERE match_id=?
                GROUP BY team_number
                """,
                (int(match_id),),
            ) as cur:
                for team, stars, destruction in await cur.fetchall():
                    result[int(team)] = (int(stars or 0), float(destruction or 0))
        return result

    async def effective_score(self, match: dict) -> dict:
        penalties = await self.get_penalties(int(match["id"]))
        a_adj = penalties.get(int(match["team_a"]), (0, 0.0))
        b_adj = penalties.get(int(match["team_b"]), (0, 0.0))
        return {
            "a_stars": int(match["team_a_stars"] or 0) + a_adj[0],
            "b_stars": int(match["team_b_stars"] or 0) + b_adj[0],
            "a_dest": float(match["team_a_destruction"] or 0) + a_adj[1],
            "b_dest": float(match["team_b_destruction"] or 0) + b_adj[1],
            "a_duration": float(match["team_a_duration"] or 0),
            "b_duration": float(match["team_b_duration"] or 0),
        }

    async def build_match_embed(self, match: dict) -> discord.Embed:
        score = await self.effective_score(match)
        status_map = {
            "waiting_clan": "En attente des Clan Cup",
            "waiting_lineup": "En attente des lineups",
            "waiting_war": "En attente de la Friendly War",
            "roster_invalid": "ROSTER INVALIDE — intervention staff",
            "live": "LIVE",
            "tiebreak": "ÉGALITÉ PARFAITE — 1v1 requis",
            "completed": "TERMINÉ",
        }
        status = status_map.get(match["status"], match["status"])

        embed = BrandedEmbed(
            title=(
                f"⚔️ {self.phase_label(match['phase'])} • "
                f"Match {match['match_order']}"
            ),
            description=(
                f"**Team {match['team_a']}**  VS  **Team {match['team_b']}**\n"
                f"État : **{status}**"
            ),
            color=discord.Color.green()
            if match["status"] == "live"
            else EMBED_COLOR,
        )
        embed.add_field(
            name=f"Team {match['team_a']}",
            value=(
                f"⭐ **{score['a_stars']}**\n"
                f"💥 **{score['a_dest']:.2f}%**\n"
                f"⏱️ **{score['a_duration']:.1f}s** moy."
            ),
            inline=True,
        )
        embed.add_field(
            name="VS",
            value="────────",
            inline=True,
        )
        embed.add_field(
            name=f"Team {match['team_b']}",
            value=(
                f"⭐ **{score['b_stars']}**\n"
                f"💥 **{score['b_dest']:.2f}%**\n"
                f"⏱️ **{score['b_duration']:.1f}s** moy."
            ),
            inline=True,
        )
        embed.add_field(
            name="Clans",
            value=(
                f"Team {match['team_a']} : `{match.get('team_a_clan_tag') or 'Non lié'}`\n"
                f"Team {match['team_b']} : `{match.get('team_b_clan_tag') or 'Non lié'}`"
            ),
            inline=False,
        )
        if match.get("winner_team"):
            embed.add_field(
                name="Vainqueur",
                value=f"🏆 **Team {match['winner_team']}**",
                inline=False,
            )
        embed.set_footer(text=f"KAI CUP • Match ID {match['id']}")
        return embed

    async def refresh_match_messages(self, guild: discord.Guild, match_id: int):
        match = await self.get_match(match_id)
        if not match:
            return
        embed = await self.build_match_embed(match)

        for key in ("match_message_id", "live_message_id"):
            message_id = match.get(key)
            if not message_id:
                continue

            if key == "match_message_id":
                channel_id = match.get("match_channel_id")
            else:
                event = await self.get_event_config(guild.id)
                channel_id = event.get("live_channel_id") if event else None

            if not channel_id:
                continue
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def default_lineup_for_team(
        self,
        guild_id: int,
        draw_id: int,
        match_id: int,
        team_number: int,
        phase: str,
    ):
        if phase == "round2":
            return
        required = self.required_starters(phase)
        members = await self.team_active_members(guild_id, draw_id, team_number)
        if len(members) < required:
            return

        async with aiosqlite.connect(DATABASE) as db:
            for slot, row in enumerate(members[:required], start=1):
                await db.execute(
                    """
                    INSERT OR IGNORE INTO cup_lineups(
                        guild_id,draw_id,match_id,team_number,
                        discord_id,player_tag,player_name,
                        slot,is_substitute,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(guild_id),
                        int(draw_id),
                        int(match_id),
                        int(team_number),
                        str(row["discord_id"]),
                        row["player_tag"],
                        row["player_name"],
                        slot,
                        0,
                        utc_now(),
                    ),
                )
            await db.commit()

    async def lineup_starters(
        self,
        match_id: int,
        team_number: int,
    ) -> list[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_lineups
                WHERE match_id=? AND team_number=? AND is_substitute=0
                ORDER BY slot ASC
                """,
                (int(match_id), int(team_number)),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def match_ready_status(self, match: dict) -> str:
        clan_a = await self.get_team_clan(
            int(match["guild_id"]),
            int(match["draw_id"]),
            int(match["team_a"]),
        )
        clan_b = await self.get_team_clan(
            int(match["guild_id"]),
            int(match["draw_id"]),
            int(match["team_b"]),
        )
        if not clan_a or not clan_b:
            return "waiting_clan"

        required = self.required_starters(match["phase"])
        lineup_a = await self.lineup_starters(int(match["id"]), int(match["team_a"]))
        lineup_b = await self.lineup_starters(int(match["id"]), int(match["team_b"]))
        if len(lineup_a) != required or len(lineup_b) != required:
            return "waiting_lineup"

        return "waiting_war"

    async def sync_match_prerequisites(self, match: dict) -> dict:
        clan_a = await self.get_team_clan(
            int(match["guild_id"]), int(match["draw_id"]), int(match["team_a"])
        )
        clan_b = await self.get_team_clan(
            int(match["guild_id"]), int(match["draw_id"]), int(match["team_b"])
        )
        status = await self.match_ready_status(match)
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                UPDATE cup_matches
                SET team_a_clan_tag=?,team_b_clan_tag=?,status=?
                WHERE id=? AND status NOT IN ('live','completed','tiebreak')
                """,
                (
                    clan_a["clan_tag"] if clan_a else None,
                    clan_b["clan_tag"] if clan_b else None,
                    status,
                    int(match["id"]),
                ),
            )
            await db.commit()
        return await self.get_match(int(match["id"]))

    async def create_match_channel(
        self,
        guild: discord.Guild,
        match_id: int,
        team_a: int,
        team_b: int,
        phase: str,
        match_order: int,
    ) -> tuple[discord.TextChannel, discord.Message, Optional[discord.Message]]:
        event = await self.ensure_event_space(guild)
        category = guild.get_channel(int(event["matches_category_id"]))
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Catégorie de matchs introuvable")

        draw = await self.get_active_draw(guild.id)
        if not draw:
            raise RuntimeError("Tirage actif introuvable")

        row_a = await self.get_team_row(guild.id, int(draw["id"]), team_a)
        row_b = await self.get_team_row(guild.id, int(draw["id"]), team_b)
        role_a = guild.get_role(int(row_a["role_id"])) if row_a and row_a.get("role_id") else None
        role_b = guild.get_role(int(row_b["role_id"])) if row_b and row_b.get("role_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        if role_a:
            overwrites[role_a] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        if role_b:
            overwrites[role_b] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        match = await self.get_match(match_id)
        channel = None
        if match.get("match_channel_id"):
            channel_id = int(match["match_channel_id"])
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    pass
        if channel is None:
            # Recover a channel created just before its ID could be committed.
            topic = f"KAI CUP • Match ID {match_id}"
            channel = discord.utils.get(guild.text_channels, topic=topic)
        if channel is None:
            channel = await guild.create_text_channel(
                f"{phase}-{match_order:02d}-t{team_a}-vs-t{team_b}"[:100],
                category=category,
                overwrites=overwrites,
                topic=f"KAI CUP • Match ID {match_id}",
                reason="KAI CUP • création match",
            )
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Le salon de match n'est pas un salon texte.")
        if str(channel.id) != str(match.get("match_channel_id")):
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE cup_matches SET match_channel_id=?,match_message_id=NULL WHERE id=?",
                    (str(channel.id), match_id),
                )
                await db.commit()
            match["match_message_id"] = None

        embed = await self.build_match_embed(match)
        content_parts = [role.mention for role in (role_a, role_b) if role]
        message = None
        if match.get("match_message_id"):
            try:
                message = await channel.fetch_message(int(match["match_message_id"]))
            except discord.NotFound:
                pass
        if message is None:
            message = await channel.send(
                content=branded_text(" ".join(content_parts) if content_parts else None), embed=embed,
            )
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute("UPDATE cup_matches SET match_message_id=? WHERE id=?",
                                 (str(message.id), match_id))
                await db.commit()

        live_channel = guild.get_channel(int(event["live_channel_id"]))
        if not isinstance(live_channel, discord.TextChannel):
            raise RuntimeError("Le salon live est indisponible : vérifier /cup event.")
        live_message = None
        if match.get("live_message_id"):
            try:
                live_message = await live_channel.fetch_message(int(match["live_message_id"]))
            except discord.NotFound:
                pass
        if live_message is None:
            live_message = await live_channel.send(embed=embed)
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute("UPDATE cup_matches SET live_message_id=? WHERE id=?",
                                 (str(live_message.id), match_id))
                await db.commit()
        return channel, message, live_message

    async def phase_is_preparing(self, match: dict) -> bool:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT status FROM cup_phase_plans WHERE guild_id=? AND draw_id=? AND phase=?",
                (str(match["guild_id"]), int(match["draw_id"]), match["phase"]),
            ) as cur:
                row = await cur.fetchone()
                return bool(row and row[0] == "preparing")

    async def create_phase_matches(
        self,
        guild: discord.Guild,
        draw_id: int,
        phase: str,
        pairs: list[tuple[int, int]],
    ) -> list[int]:
        pairs = [(int(a), int(b)) for a, b in pairs]
        flat = [t for pair in pairs for t in pair]
        if (phase not in {"round1", "round2", "playin", "semifinal", "final"}
                or not pairs or len(flat) != len(set(flat))):
            raise ValueError("Phase ou paires invalides : chaque équipe doit jouer une seule fois.")

        expected_roster = self.required_roster_size(phase)
        roster_issues = await self.roster_integrity_issues(
            guild.id,
            draw_id,
            flat,
            expected_roster,
        )
        if roster_issues:
            raise RosterIntegrityError(phase, expected_roster, roster_issues)

        clans = {t: await self.get_team_clan(guild.id, draw_id, t) for t in flat}
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM cup_phase_plans WHERE guild_id=? AND draw_id=? AND phase=?",
                (str(guild.id), draw_id, phase),
            ) as cur:
                plan = await cur.fetchone()
            if plan and json.loads(plan["pairs_json"]) != [list(p) for p in pairs]:
                raise ValueError("Les paires diffèrent de la phase enregistrée : reprise annulée.")
            await db.execute(
                "INSERT OR IGNORE INTO cup_phase_plans "
                "(guild_id,draw_id,phase,pairs_json,status,created_at) VALUES(?,?,?,?,'preparing',?)",
                (str(guild.id), draw_id, phase, json.dumps(pairs), utc_now()),
            )
            for order, (team_a, team_b) in enumerate(pairs, 1):
                clan_a, clan_b = clans[team_a], clans[team_b]
                await db.execute(
                    "INSERT OR IGNORE INTO cup_matches "
                    "(guild_id,draw_id,phase,match_order,team_a,team_b,status,"
                    "team_a_clan_tag,team_b_clan_tag,created_at) "
                    "VALUES(?,?,?,?,?,?,'waiting_clan',?,?,?)",
                    (str(guild.id), draw_id, phase, order, team_a, team_b,
                     clan_a["clan_tag"] if clan_a else None,
                     clan_b["clan_tag"] if clan_b else None, utc_now()),
                )
            async with db.execute(
                "SELECT * FROM cup_matches WHERE guild_id=? AND draw_id=? AND phase=? ORDER BY match_order",
                (str(guild.id), draw_id, phase),
            ) as cur:
                matches = [dict(r) for r in await cur.fetchall()]
            if [(int(m["team_a"]), int(m["team_b"])) for m in matches] != pairs:
                raise ValueError("Les matchs existants ne correspondent pas au plan de phase.")
            await db.commit()

        ids = [int(m["id"]) for m in matches]
        if plan and plan["status"] == "ready":
            return ids
        for match in matches:
            match_id = int(match["id"])
            if match["status"] in {"completed", "live", "tiebreak"}:
                continue
            for team in (match["team_a"], match["team_b"]):
                await self.default_lineup_for_team(guild.id, draw_id, match_id, team, phase)
            status = await self.match_ready_status(await self.get_match(match_id))
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute("UPDATE cup_matches SET status=? WHERE id=?", (status, match_id))
                await db.commit()
            if not match.get("match_message_id") or not match.get("live_message_id"):
                channel, message, live_message = await self.create_match_channel(
                    guild, match_id, match["team_a"], match["team_b"], phase, match["match_order"]
                )
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        "UPDATE cup_matches SET match_channel_id=?,match_message_id=?,live_message_id=? WHERE id=?",
                        (str(channel.id), str(message.id),
                         str(live_message.id) if live_message else None, match_id),
                    )
                    await db.commit()
        await self.set_cup_state(guild.id, draw_id, phase=phase, status="running")
        await self.publish_bracket(guild)
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE cup_phase_plans SET status='ready' WHERE guild_id=? AND draw_id=? AND phase=?",
                (str(guild.id), draw_id, phase),
            )
            await db.commit()
        return ids

    async def resume_phase_creation(self, guild: discord.Guild, draw_id: int) -> bool:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_phase_plans WHERE guild_id=? AND draw_id=? "
                "AND status='preparing' ORDER BY created_at LIMIT 1",
                (str(guild.id), draw_id),
            ) as cur:
                plan = await cur.fetchone()
        if not plan:
            return False
        await self.create_phase_matches(guild, draw_id, plan["phase"], json.loads(plan["pairs_json"]))
        return True

    async def validate_war_roster(
        self,
        match: dict,
        war: dict,
    ) -> tuple[bool, str]:
        tag_a = normalize_tag(match.get("team_a_clan_tag") or "")
        tag_b = normalize_tag(match.get("team_b_clan_tag") or "")

        side1 = war.get("clan") or {}
        side2 = war.get("opponent") or {}
        side1_tag = normalize_tag(side1.get("tag") or "")
        side2_tag = normalize_tag(side2.get("tag") or "")

        if {side1_tag, side2_tag} != {tag_a, tag_b}:
            return False, "La guerre détectée n'oppose pas les deux Clan Cup officiels."

        required = self.required_starters(match["phase"])
        lineup_a = await self.lineup_starters(int(match["id"]), int(match["team_a"]))
        lineup_b = await self.lineup_starters(int(match["id"]), int(match["team_b"]))
        allowed_a = {normalize_tag(x["player_tag"]) for x in lineup_a}
        allowed_b = {normalize_tag(x["player_tag"]) for x in lineup_b}

        if side1_tag == tag_a:
            members_a = {normalize_tag(x.get("tag") or "") for x in side1.get("members", [])}
            members_b = {normalize_tag(x.get("tag") or "") for x in side2.get("members", [])}
        else:
            members_a = {normalize_tag(x.get("tag") or "") for x in side2.get("members", [])}
            members_b = {normalize_tag(x.get("tag") or "") for x in side1.get("members", [])}

        if len(allowed_a) != required or len(allowed_b) != required:
            return False, "Lineup incomplète."

        if members_a != allowed_a:
            return False, f"Roster invalide Team {match['team_a']}."
        if members_b != allowed_b:
            return False, f"Roster invalide Team {match['team_b']}."

        return True, ""

    @staticmethod
    def side_average_duration(side: dict) -> float:
        durations = []
        for member in side.get("members", []) or []:
            for attack in member.get("attacks", []) or []:
                duration = float(attack.get("duration", 0) or 0)
                if duration > 0:
                    durations.append(duration)
        return sum(durations) / len(durations) if durations else 0.0

    async def save_war_attacks(self, match: dict, war: dict):
        tag_a = normalize_tag(match.get("team_a_clan_tag") or "")
        side1 = war.get("clan") or {}
        side2 = war.get("opponent") or {}

        for side in (side1, side2):
            side_tag = normalize_tag(side.get("tag") or "")
            team_number = (
                int(match["team_a"])
                if side_tag == tag_a
                else int(match["team_b"])
            )
            defenders = side2 if side is side1 else side1
            defender_map = {
                x.get("tag"): x
                for x in defenders.get("members", []) or []
                if x.get("tag")
            }

            async with aiosqlite.connect(DATABASE) as db:
                for member in side.get("members", []) or []:
                    for attack in member.get("attacks", []) or []:
                        defender = defender_map.get(attack.get("defenderTag"), {})
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO cup_attacks(
                                guild_id,draw_id,match_id,phase,team_number,
                                attacker_tag,attacker_name,defender_tag,defender_name,
                                attack_order,stars,destruction,duration,created_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                str(match["guild_id"]),
                                int(match["draw_id"]),
                                int(match["id"]),
                                match["phase"],
                                team_number,
                                attack.get("attackerTag") or member.get("tag"),
                                member.get("name", "Unknown"),
                                attack.get("defenderTag") or "",
                                defender.get("name", "Unknown"),
                                int(attack.get("order", 0) or 0),
                                int(attack.get("stars", 0) or 0),
                                float(attack.get("destructionPercentage", 0) or 0),
                                float(attack.get("duration", 0) or 0),
                                utc_now(),
                            ),
                        )
                await db.commit()

    async def update_match_from_war(
        self,
        guild: discord.Guild,
        match: dict,
        war: dict,
    ):
        tag_a = normalize_tag(match["team_a_clan_tag"])
        side1 = war.get("clan") or {}
        side2 = war.get("opponent") or {}

        if normalize_tag(side1.get("tag") or "") == tag_a:
            a_side, b_side = side1, side2
        else:
            a_side, b_side = side2, side1

        a_stars = int(a_side.get("stars", 0) or 0)
        b_stars = int(b_side.get("stars", 0) or 0)
        a_dest = float(a_side.get("destructionPercentage", 0) or 0)
        b_dest = float(b_side.get("destructionPercentage", 0) or 0)
        a_dur = self.side_average_duration(a_side)
        b_dur = self.side_average_duration(b_side)

        war_key = self.cup_war_key(war)
        state = war.get("state")
        status = "live" if state == "inWar" else match["status"]

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                UPDATE cup_matches
                SET war_key=?,team_a_stars=?,team_b_stars=?,
                    team_a_destruction=?,team_b_destruction=?,
                    team_a_duration=?,team_b_duration=?,
                    status=?,
                    started_at=COALESCE(started_at,?)
                WHERE id=?
                """,
                (
                    war_key,
                    a_stars,
                    b_stars,
                    a_dest,
                    b_dest,
                    a_dur,
                    b_dur,
                    status,
                    utc_now(),
                    int(match["id"]),
                ),
            )
            await db.commit()

        await self.save_war_attacks(match, war)
        await self.refresh_match_messages(guild, int(match["id"]))

        if state == "warEnded":
            refreshed = await self.get_match(int(match["id"]))
            await self.finalize_match(guild, refreshed)

    async def decide_winner(
        self,
        match: dict,
        *,
        forced_winner: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int], str]:
        if forced_winner in {int(match["team_a"]), int(match["team_b"])}:
            loser = (
                int(match["team_b"])
                if forced_winner == int(match["team_a"])
                else int(match["team_a"])
            )
            return forced_winner, loser, "staff"

        score = await self.effective_score(match)
        a = int(match["team_a"])
        b = int(match["team_b"])

        if score["a_stars"] != score["b_stars"]:
            winner = a if score["a_stars"] > score["b_stars"] else b
            return winner, b if winner == a else a, "stars"

        if round(score["a_dest"], 3) != round(score["b_dest"], 3):
            winner = a if score["a_dest"] > score["b_dest"] else b
            return winner, b if winner == a else a, "destruction"

        # 0 signifie durée indisponible : dans ce cas on ne fabrique pas de départage.
        if (
            score["a_duration"] > 0
            and score["b_duration"] > 0
            and round(score["a_duration"], 3) != round(score["b_duration"], 3)
        ):
            winner = a if score["a_duration"] < score["b_duration"] else b
            return winner, b if winner == a else a, "duration"

        return None, None, "tiebreak"

    async def finalize_match(
        self,
        guild: discord.Guild,
        match: dict,
        forced_winner: Optional[int] = None,
    ):
        if not match or match["status"] == "completed":
            return

        winner, loser, reason = await self.decide_winner(
            match,
            forced_winner=forced_winner,
        )

        if not winner:
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE cup_matches SET status='tiebreak' WHERE id=?",
                    (int(match["id"]),),
                )
                await db.commit()
            await self.refresh_match_messages(guild, int(match["id"]))
            await self.cup_log(
                guild,
                f"⚠️ Match {match['id']} : égalité parfaite, 1v1 requis.",
            )
            return

        now = utc_now()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE cup_matches
                SET status='completed',winner_team=?,loser_team=?,ended_at=?
                WHERE id=? AND status!='completed'
                """,
                (winner, loser, now, int(match["id"])),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO cup_match_finalizations(
                    match_id,guild_id,draw_id,status,created_at,updated_at
                ) VALUES(?,?,?,'pending',?,?)
                """,
                (
                    int(match["id"]),
                    str(guild.id),
                    int(match["draw_id"]),
                    now,
                    now,
                ),
            )
            await db.commit()

        await self.resume_match_finalization(guild, int(match["id"]), reason)

    async def get_match_finalization(self, match_id: int) -> Optional[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_match_finalizations WHERE match_id=?",
                (int(match_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_finalization_step(
        self,
        match_id: int,
        column: str,
    ):
        allowed = {
            "result_announced",
            "progression_done",
            "champion_done",
            "bracket_done",
            "channel_cleanup_done",
        }
        if column not in allowed:
            raise ValueError("Étape de finalisation inconnue")
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                f"UPDATE cup_match_finalizations SET {column}=1,last_error=NULL,updated_at=? WHERE match_id=?",
                (utc_now(), int(match_id)),
            )
            await db.commit()

    async def save_finalization_error(self, match_id: int, exc: Exception):
        detail = f"{type(exc).__name__}: {exc}"[:1000]
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE cup_match_finalizations SET status='pending',last_error=?,updated_at=? WHERE match_id=?",
                (detail, utc_now(), int(match_id)),
            )
            await db.commit()

    async def announce_match_result(self, guild: discord.Guild, match: dict):
        event = await self.get_event_config(guild.id)
        if not event or not event.get("results_channel_id"):
            raise RuntimeError("Salon de résultats non configuré")
        channel = guild.get_channel(int(event["results_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Salon de résultats introuvable")
        score = await self.effective_score(match)
        embed = BrandedEmbed(
            title=f"🏆 Team {match['winner_team']} remporte le match",
            description=(
                f"**{self.phase_label(match['phase'])} • Match {match['match_order']}**\n"
                f"Team {match['team_a']} : **{score['a_stars']}⭐ • {score['a_dest']:.2f}%**\n"
                f"Team {match['team_b']} : **{score['b_stars']}⭐ • {score['b_dest']:.2f}%**"
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"KAI CUP • RESULT MATCH {match['id']}")
        kind = f"result:{match['id']}"
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT message_id FROM cup_public_messages WHERE guild_id=? AND draw_id=? AND kind=?",
                (str(guild.id), int(match["draw_id"]), kind),
            ) as cur:
                row = await cur.fetchone()
            if row:
                try:
                    await channel.fetch_message(int(row[0]))
                    return
                except discord.NotFound:
                    pass
            msg = await channel.send(embed=embed)
            await db.execute(
                "INSERT OR REPLACE INTO cup_public_messages(guild_id,draw_id,kind,message_id) VALUES(?,?,?,?)",
                (str(guild.id), int(match["draw_id"]), kind, str(msg.id)),
            )
            await db.commit()

    async def resume_match_finalization(
        self,
        guild: discord.Guild,
        match_id: int,
        reason: str = "score",
    ) -> bool:
        async with self.finalization_lock:
            match = await self.get_match(match_id)
            job = await self.get_match_finalization(match_id)
            if not match or match["status"] != "completed" or not job:
                return False
            if job["status"] == "done":
                return True
            try:
                await self.refresh_match_messages(guild, match_id)

                if not job["result_announced"]:
                    await self.announce_match_result(guild, match)
                    await self.set_finalization_step(match_id, "result_announced")
                    job["result_announced"] = 1

                if not job["progression_done"]:
                    if match["phase"] in {"round1", "round2"}:
                        await self.open_draft(guild, match)
                    else:
                        await self.eliminate_team(
                            guild, int(match["draw_id"]), int(match["loser_team"])
                        )
                    await self.set_finalization_step(match_id, "progression_done")
                    job["progression_done"] = 1

                if not job["champion_done"]:
                    if match["phase"] == "final":
                        await self.set_cup_state(
                            guild.id,
                            int(match["draw_id"]),
                            phase="final",
                            status="completed",
                            champion_team=int(match["winner_team"]),
                        )
                        await self.announce_champion(
                            guild, int(match["draw_id"]), int(match["winner_team"])
                        )
                    await self.set_finalization_step(match_id, "champion_done")
                    job["champion_done"] = 1

                if not job["bracket_done"]:
                    await self.publish_bracket(guild)
                    await self.set_finalization_step(match_id, "bracket_done")
                    job["bracket_done"] = 1

                if not job["channel_cleanup_done"]:
                    if match.get("match_channel_id"):
                        channel = guild.get_channel(int(match["match_channel_id"]))
                        if isinstance(channel, discord.TextChannel):
                            try:
                                await channel.delete(reason="KAI CUP • match terminé")
                            except discord.NotFound:
                                pass
                    await self.set_finalization_step(match_id, "channel_cleanup_done")

                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        "UPDATE cup_match_finalizations SET status='done',last_error=NULL,updated_at=? WHERE match_id=?",
                        (utc_now(), int(match_id)),
                    )
                    await db.commit()
                return True
            except Exception as exc:
                await self.save_finalization_error(match_id, exc)
                raise

    async def draft_candidates(self, match_id: int) -> list[dict]:
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_drafts WHERE match_id=?",
                (int(match_id),),
            ) as cur:
                draft = await cur.fetchone()
                if not draft:
                    return []
                loser_team = int(draft["loser_team"])
                draw_id = int(draft["draw_id"])
                guild_id = int(draft["guild_id"])

        return await self.team_active_members(guild_id, draw_id, loser_team)

    async def open_draft(self, guild: discord.Guild, match: dict):
        from datetime import timedelta

        deadline_dt = datetime.now(timezone.utc) + timedelta(minutes=5)
        deadline = deadline_dt.isoformat()

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                INSERT OR IGNORE INTO cup_drafts(
                    match_id,guild_id,draw_id,winner_team,loser_team,
                    status,selected_player_tag,deadline,message_id,
                    created_at,resolved_at
                ) VALUES(?,?,?,?,?,'pending',NULL,?,NULL,?,NULL)
                """,
                (
                    int(match["id"]),
                    str(guild.id),
                    int(match["draw_id"]),
                    int(match["winner_team"]),
                    int(match["loser_team"]),
                    deadline,
                    utc_now(),
                ),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM cup_drafts WHERE match_id=?",
                (int(match["id"]),),
            ) as draft_cur:
                draft = await draft_cur.fetchone()

        if not draft or draft["status"] != "pending":
            return
        if draft["message_id"]:
            return

        winner_row = await self.get_team_row(
            guild.id, int(match["draw_id"]), int(match["winner_team"])
        )
        if not winner_row or not winner_row.get("channel_id"):
            raise RuntimeError("Salon de l'équipe gagnante non configuré")

        channel = guild.get_channel(int(winner_row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Salon de l'équipe gagnante introuvable")

        candidates = await self.draft_candidates(int(match["id"]))
        if not candidates:
            raise RuntimeError("Aucun joueur admissible au recrutement")
        view = CupDraftView(self, int(match["id"]), candidates)
        captain_id = int(winner_row["captain_discord_id"])

        embed = BrandedEmbed(
            title="🎯 RECRUTEMENT KAI CUP",
            description=(
                f"Vous avez éliminé la **Team {match['loser_team']}**.\n"
                "Le capitaine dispose de **5 minutes** pour recruter un joueur.\n"
                "Sans choix dans le délai, le bot sélectionnera automatiquement "
                "un joueur admissible."
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"KAI CUP • DRAFT MATCH {match['id']}")

        msg = await channel.send(
            content=branded_text(f"<@{captain_id}>"),
            embed=embed,
            view=view,
        )

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "UPDATE cup_drafts SET message_id=? WHERE match_id=?",
                (str(msg.id), int(match["id"])),
            )
            await db.commit()

    async def execute_draft(
        self,
        guild: discord.Guild,
        match_id: int,
        player_tag: str,
        actor_id: Optional[int] = None,
        interaction: Optional[discord.Interaction] = None,
        automatic: bool = False,
    ):
        async with self.draft_lock:
            async with aiosqlite.connect(DATABASE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM cup_drafts WHERE match_id=?",
                    (int(match_id),),
                ) as cur:
                    draft = await cur.fetchone()

                if not draft or int(draft["guild_id"]) != guild.id or draft["status"] != "pending":
                    if interaction:
                        await interaction.followup.send(
                            branded_text("❌ Ce recrutement est déjà terminé."),
                            ephemeral=True,
                        )
                    return False

                winner_team = int(draft["winner_team"])
                loser_team = int(draft["loser_team"])
                draw_id = int(draft["draw_id"])

                winner_row = await self.get_team_row(
                    guild.id, draw_id, winner_team
                )
                if not winner_row:
                    return False

                if not automatic:
                    member = guild.get_member(int(actor_id or 0))
                    is_staff = bool(member and member.guild_permissions.manage_guild)
                    if int(winner_row["captain_discord_id"]) != int(actor_id or 0) and not is_staff:
                        if interaction:
                            await interaction.followup.send(
                                branded_text("❌ Seul le capitaine de la Team gagnante ou le staff peut choisir."),
                                ephemeral=True,
                            )
                        return False

                async with db.execute(
                    """
                    SELECT *
                    FROM cup_team_members
                    WHERE guild_id=? AND draw_id=? AND team_number=? AND player_tag=?
                    """,
                    (
                        str(guild.id),
                        draw_id,
                        loser_team,
                        normalize_tag(player_tag),
                    ),
                ) as cur:
                    chosen = await cur.fetchone()

                if not chosen:
                    if interaction:
                        await interaction.followup.send(
                            branded_text("❌ Joueur non admissible."),
                            ephemeral=True,
                        )
                    return False

                async with db.execute(
                    """
                    SELECT COALESCE(MAX(member_order),0)+1
                    FROM cup_team_members
                    WHERE guild_id=? AND draw_id=? AND team_number=?
                    """,
                    (str(guild.id), draw_id, winner_team),
                ) as cur:
                    next_order = int((await cur.fetchone())[0] or 1)

                await db.execute(
                    """
                    UPDATE cup_team_members
                    SET team_number=?,member_order=?,is_captain=0
                    WHERE id=?
                    """,
                    (winner_team, next_order, int(chosen["id"])),
                )
                await db.execute(
                    """
                    UPDATE cup_drafts
                    SET status='resolved',selected_player_tag=?,resolved_at=?
                    WHERE match_id=?
                    """,
                    (normalize_tag(player_tag), utc_now(), int(match_id)),
                )
                await db.commit()

            winner_role = (
                guild.get_role(int(winner_row["role_id"]))
                if winner_row.get("role_id")
                else None
            )
            loser_row = await self.get_team_row(guild.id, draw_id, loser_team)
            loser_role = (
                guild.get_role(int(loser_row["role_id"]))
                if loser_row and loser_row.get("role_id")
                else None
            )
            discord_member = guild.get_member(int(chosen["discord_id"]))

            if discord_member:
                try:
                    if loser_role:
                        await discord_member.remove_roles(
                            loser_role,
                            reason="KAI CUP • recrutement",
                        )
                    if winner_role:
                        await discord_member.add_roles(
                            winner_role,
                            reason="KAI CUP • recrutement",
                        )
                except (discord.Forbidden, discord.HTTPException):
                    pass

                clan = await self.get_team_clan(guild.id, draw_id, winner_team)
                if clan:
                    try:
                        await discord_member.send(
                            branded_text(f"🏆 **KAI CUP** — Tu rejoins la **Team {winner_team}**.\n"
                            f"Clan Cup : **{clan['clan_name']}** `{clan['clan_tag']}`")
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

            if winner_row.get("channel_id"):
                channel = guild.get_channel(int(winner_row["channel_id"]))
                if isinstance(channel, discord.TextChannel):
                    await channel.send(
                        branded_text(f"✅ <@{chosen['discord_id']}> rejoint officiellement "
                        f"la **Team {winner_team}**.")
                    )

            await self.eliminate_team(guild, draw_id, loser_team)

            await self.audit(
                guild.id,
                actor_id,
                "draft_resolved_auto" if automatic else "draft_resolved",
                (
                    f"match={match_id}; winner={winner_team}; loser={loser_team}; "
                    f"player={chosen['player_name']} {chosen['player_tag']}"
                ),
            )

            if interaction:
                await interaction.followup.send(
                    branded_text(f"✅ **{chosen['player_name']}** rejoint la Team {winner_team}."),
                    ephemeral=True,
                )
            return True

    async def eliminate_team(
        self,
        guild: discord.Guild,
        draw_id: int,
        team_number: int,
    ):
        row = await self.get_team_row(guild.id, draw_id, team_number)
        if not row:
            return

        if row["status"] != "eliminated":
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE cup_teams
                    SET status='eliminated'
                    WHERE guild_id=? AND draw_id=? AND team_number=?
                    """,
                    (str(guild.id), int(draw_id), int(team_number)),
                )
                await db.commit()

        if row.get("channel_id"):
            channel = guild.get_channel(int(row["channel_id"]))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.delete(reason="KAI CUP • Team éliminée")
                except discord.NotFound:
                    pass

        if row.get("role_id"):
            role = guild.get_role(int(row["role_id"]))
            if role:
                try:
                    await role.delete(reason="KAI CUP • Team éliminée")
                except discord.NotFound:
                    pass

    async def process_match_live(self, guild: discord.Guild, match: dict):
        async with self.match_lock:
            # Reload after acquiring the lock: the staff may have finished this match.
            match = await self.get_match(int(match["id"]))
            if not match:
                return
            await self._process_match_live_locked(guild, match)

    async def _process_match_live_locked(self, guild: discord.Guild, match: dict):
        if await self.phase_is_preparing(match):
            return
        if match["status"] == "completed":
            await self.resume_match_finalization(guild, int(match["id"]))
            return
        if match["status"] == "tiebreak":
            return

        match = await self.sync_match_prerequisites(match)
        if not match or match["status"] not in {"waiting_war", "live", "roster_invalid"}:
            await self.refresh_match_messages(guild, int(match["id"]))
            return

        clan_a_tag = match.get("team_a_clan_tag")
        clan_b_tag = match.get("team_b_clan_tag")
        if not clan_a_tag or not clan_b_tag:
            return

        war = await self.current_war(clan_a_tag)
        if not war:
            return

        state = war.get("state")
        if state not in {"inWar", "warEnded"}:
            return

        tags = {
            normalize_tag((war.get("clan") or {}).get("tag") or ""),
            normalize_tag((war.get("opponent") or {}).get("tag") or ""),
        }
        if tags != {normalize_tag(clan_a_tag), normalize_tag(clan_b_tag)}:
            return

        valid, reason = await self.validate_war_roster(match, war)
        if not valid:
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE cup_matches SET status='roster_invalid' WHERE id=?",
                    (int(match["id"]),),
                )
                await db.commit()
            await self.refresh_match_messages(guild, int(match["id"]))
            await self.cup_log(
                guild,
                f"⚠️ Match {match['id']} : {reason}",
            )
            return

        await self.update_match_from_war(guild, match, war)

    async def process_pending_drafts(self, guild: discord.Guild):
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_drafts
                WHERE guild_id=? AND status='pending'
                """,
                (str(guild.id),),
            ) as cur:
                rows = [dict(x) for x in await cur.fetchall()]

        for draft in rows:
            try:
                deadline = datetime.fromisoformat(draft["deadline"])
            except Exception:
                continue
            if deadline > now:
                continue

            candidates = await self.draft_candidates(int(draft["match_id"]))
            if not candidates:
                continue
            chosen = random.choice(candidates)
            await self.execute_draft(
                guild,
                int(draft["match_id"]),
                chosen["player_tag"],
                actor_id=None,
                automatic=True,
            )

    async def process_pending_finalizations(self, guild: discord.Guild, draw_id: int):
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT match_id FROM cup_match_finalizations
                WHERE guild_id=? AND draw_id=? AND status='pending'
                ORDER BY match_id
                """,
                (str(guild.id), int(draw_id)),
            ) as cur:
                match_ids = [int(row[0]) for row in await cur.fetchall()]
        for match_id in match_ids:
            try:
                await self.resume_match_finalization(guild, match_id)
            except Exception as exc:
                try:
                    await self.cup_log(
                        guild,
                        f"❌ Finalisation match {match_id} : {type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass

    @tasks.loop(seconds=45)
    async def cup_live_loop(self):
        for guild in list(self.bot.guilds):
            try:
                recorded_draw = await self.registration_draw(guild.id)
                if recorded_draw and recorded_draw["status"] in {"preparing", "error"}:
                    try:
                        async with self.draw_lock:
                            await self.resume_draw_creation(guild, int(recorded_draw["id"]))
                    except Exception as exc:
                        try:
                            await self.cup_log(
                                guild,
                                f"❌ Reprise tirage {recorded_draw['draw_code']} : {type(exc).__name__}: {exc}",
                            )
                        except Exception:
                            pass
                        continue
                draw = await self.get_active_draw(guild.id)
                if not draw:
                    continue

                async with aiosqlite.connect(DATABASE) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        """
                        SELECT *
                        FROM cup_matches
                        WHERE guild_id=? AND draw_id=?
                          AND status NOT IN ('completed','tiebreak')
                        ORDER BY id ASC
                        """,
                        (str(guild.id), int(draw["id"])),
                    ) as cur:
                        matches = [dict(x) for x in await cur.fetchall()]

                for match in matches:
                    try:
                        await self.process_match_live(guild, match)
                    except Exception as exc:
                        try:
                            await self.cup_log(
                                guild, f"❌ Match {match['id']} : {type(exc).__name__}: {exc}"
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(0.25)

                await self.process_pending_finalizations(guild, int(draw["id"]))
                await self.process_pending_drafts(guild)
            except Exception as exc:
                try:
                    await self.cup_log(
                        guild,
                        f"❌ Live loop : {type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass

    @cup_live_loop.before_loop
    async def before_cup_live_loop(self):
        await self.bot.wait_until_ready()

    async def active_team_numbers(
        self,
        guild_id: int,
        draw_id: int,
    ) -> list[int]:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT team_number
                FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND status='active'
                ORDER BY team_number ASC
                """,
                (str(guild_id), int(draw_id)),
            ) as cur:
                return [int(x[0]) for x in await cur.fetchall()]

    async def unresolved_drafts_for_phase(
        self,
        guild_id: int,
        draw_id: int,
        phase: str,
    ) -> int:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT COUNT(*)
                FROM cup_drafts d
                JOIN cup_matches m ON m.id=d.match_id
                WHERE d.guild_id=? AND d.draw_id=? AND m.phase=?
                  AND d.status='pending'
                """,
                (str(guild_id), int(draw_id), phase),
            ) as cur:
                return int((await cur.fetchone())[0] or 0)

    async def phase_completed(
        self,
        guild_id: int,
        draw_id: int,
        phase: str,
    ) -> bool:
        matches = await self.get_phase_matches(guild_id, draw_id, phase)
        return bool(matches) and all(m["status"] == "completed" for m in matches)

    async def phase_winners(
        self,
        guild_id: int,
        draw_id: int,
        phase: str,
    ) -> list[int]:
        matches = await self.get_phase_matches(guild_id, draw_id, phase)
        return [
            int(m["winner_team"])
            for m in matches
            if m.get("winner_team") is not None
        ]

    async def calculate_seeds(
        self,
        guild_id: int,
        draw_id: int,
        teams: list[int],
    ) -> list[dict]:
        teams = [int(team) for team in teams]
        if not teams or len(teams) != len(set(teams)):
            raise SeedingIntegrityError(
                "Liste des qualifiés vide ou dupliquée : classement annulé."
            )

        phase_matches = {
            phase: await self.get_phase_matches(guild_id, draw_id, phase)
            for phase in ("round1", "round2")
        }
        stats = []
        missing_data = []
        for team in teams:
            stars = 0
            destruction = 0.0
            durations = []

            for phase in ("round1", "round2"):
                appearances = [
                    match
                    for match in phase_matches[phase]
                    if team in (int(match["team_a"]), int(match["team_b"]))
                ]
                if len(appearances) != 1 or appearances[0]["status"] != "completed":
                    missing_data.append(
                        f"Team {team}/{self.phase_label(phase)} : "
                        f"{len(appearances)} match, statut "
                        f"{appearances[0]['status'] if len(appearances) == 1 else 'invalide'}"
                    )
                    continue

                match = appearances[0]
                score = await self.effective_score(match)
                side = "a" if int(match["team_a"]) == team else "b"
                duration = float(score[f"{side}_duration"])
                if duration <= 0 or not math.isfinite(duration):
                    missing_data.append(
                        f"Team {team}/{self.phase_label(phase)} : durée absente"
                    )
                    continue
                stars += int(score[f"{side}_stars"])
                destruction += float(score[f"{side}_dest"])
                durations.append(duration)

            if len(durations) != 2:
                continue
            avg_duration = sum(durations) / len(durations)
            stats.append(
                {
                    "team": team,
                    "stars": stars,
                    "destruction": destruction,
                    "avg_duration": avg_duration,
                }
            )

        if missing_data:
            raise SeedingIntegrityError(
                "Données de classement incomplètes — " + "; ".join(missing_data)
            )

        tied: dict[tuple[int, float, float], list[int]] = {}
        for row in stats:
            metric = (
                int(row["stars"]),
                round(float(row["destruction"]), 3),
                round(float(row["avg_duration"]), 3),
            )
            tied.setdefault(metric, []).append(int(row["team"]))
        perfect_ties = [group for group in tied.values() if len(group) > 1]
        if perfect_ties:
            groups = "; ".join(
                "Teams " + ", ".join(str(team) for team in sorted(group))
                for group in perfect_ties
            )
            raise SeedingIntegrityError(
                "Égalité parfaite au classement (étoiles, destruction et durée) : "
                f"{groups}. Un départage staff est requis avant les play-ins."
            )

        stats.sort(
            key=lambda x: (
                -x["stars"],
                -round(x["destruction"], 3),
                round(x["avg_duration"], 3),
                x["team"],
            )
        )

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "DELETE FROM cup_seeds WHERE guild_id=? AND draw_id=?",
                (str(guild_id), int(draw_id)),
            )
            for seed, row in enumerate(stats, start=1):
                row["seed"] = seed
                await db.execute(
                    """
                    INSERT INTO cup_seeds(
                        guild_id,draw_id,team_number,seed,
                        stars,destruction,avg_duration,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(guild_id),
                        int(draw_id),
                        int(row["team"]),
                        seed,
                        int(row["stars"]),
                        float(row["destruction"]),
                        float(row["avg_duration"]),
                        utc_now(),
                    ),
                )
            await db.commit()

        return stats

    async def seeds_map(self, guild_id: int, draw_id: int) -> dict[int, int]:
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT seed,team_number
                FROM cup_seeds
                WHERE guild_id=? AND draw_id=?
                """,
                (str(guild_id), int(draw_id)),
            ) as cur:
                return {int(seed): int(team) for seed, team in await cur.fetchall()}

    async def publish_bracket(self, guild: discord.Guild):
        event = await self.get_event_config(guild.id)
        draw = await self.get_active_draw(guild.id)
        if not event or not draw or not event.get("bracket_channel_id"):
            return

        channel = guild.get_channel(int(event["bracket_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return

        lines = []
        for phase in ("round1", "round2", "playin", "semifinal", "final"):
            matches = await self.get_phase_matches(guild.id, int(draw["id"]), phase)
            if not matches:
                continue
            lines.append(f"**{self.phase_label(phase)}**")
            for m in matches:
                if m["status"] == "completed":
                    result = f" → 🏆 Team {m['winner_team']}"
                else:
                    result = f" • {m['status']}"
                lines.append(
                    f"`M{m['match_order']:02d}` Team {m['team_a']} vs Team {m['team_b']}{result}"
                )
            lines.append("")

        seeds = await self.seeds_map(guild.id, int(draw["id"]))
        if seeds:
            lines.append("**SEEDS**")
            for seed in sorted(seeds):
                lines.append(f"Seed {seed} → Team {seeds[seed]}")

        state = await self.get_cup_state(guild.id, int(draw["id"]))
        if state.get("champion_team"):
            lines.append("")
            lines.append(f"🏆 **CHAMPION : TEAM {state['champion_team']}**")

        embed = BrandedEmbed(
            title="🌳 KAI CUP 2026 • BRACKET",
            description="\n".join(lines) if lines else "Le tournoi n'a pas encore commencé.",
            color=EMBED_COLOR,
        )

        # Garde un seul message de bracket quand possible.
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS cup_public_messages(
                    guild_id TEXT NOT NULL,
                    draw_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY(guild_id, draw_id, kind)
                )
                """
            )
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT message_id FROM cup_public_messages
                WHERE guild_id=? AND draw_id=? AND kind='bracket'
                """,
                (str(guild.id), int(draw["id"])),
            ) as cur:
                row = await cur.fetchone()

            if row:
                try:
                    msg = await channel.fetch_message(int(row["message_id"]))
                    await msg.edit(embed=embed)
                    await db.commit()
                    return
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            msg = await channel.send(embed=embed)
            await db.execute(
                """
                INSERT OR REPLACE INTO cup_public_messages(
                    guild_id,draw_id,kind,message_id
                ) VALUES(?,?,?,?)
                """,
                (str(guild.id), int(draw["id"]), "bracket", str(msg.id)),
            )
            await db.commit()

    async def announce_champion(
        self,
        guild: discord.Guild,
        draw_id: int,
        champion_team: int,
    ):
        event = await self.get_event_config(guild.id)
        if not event:
            return
        channel = guild.get_channel(int(event["results_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Salon de résultats introuvable pour annoncer le champion")

        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                "SELECT message_id FROM cup_public_messages "
                "WHERE guild_id=? AND draw_id=? AND kind='champion'",
                (str(guild.id), int(draw_id)),
            ) as cur:
                existing = await cur.fetchone()
        if existing:
            try:
                await channel.fetch_message(int(existing[0]))
                return
            except discord.NotFound:
                pass

        members = await self.team_active_members(
            guild.id, draw_id, champion_team
        )
        roster = "\n".join(
            f"• <@{row['discord_id']}> — **{row['player_name']}**"
            for row in members
        )
        msg = await channel.send(
            embed=BrandedEmbed(
                title="🏆 CHAMPIONS KAI CUP 2026",
                description=(
                    f"## TEAM {champion_team}\n"
                    f"{roster}\n\n"
                    "Les cinq joueurs sont sacrés **Champions KAI CUP 2026**.\n"
                    "Le classement MVP peut être affiché avec `/cup mvp`."
                ),
                color=discord.Color.gold(),
            )
        )
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cup_public_messages(guild_id,draw_id,kind,message_id) "
                "VALUES(?,?,'champion',?)",
                (str(guild.id), int(draw_id), str(msg.id)),
            )
            await db.commit()

    async def mvp_ranking(
        self,
        guild_id: int,
        draw_id: int,
        champion_team: int,
    ) -> list[dict]:
        members = await self.team_active_members(
            guild_id,
            draw_id,
            champion_team,
        )
        eligible = {
            normalize_tag(row["player_tag"]): row["player_name"]
            for row in members
        }
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT attacker_tag,
                       MAX(attacker_name) AS attacker_name,
                       SUM(stars) AS stars,
                       SUM(destruction) AS destruction,
                       SUM(duration) AS duration,
                       COUNT(*) AS attacks
                FROM cup_attacks
                WHERE guild_id=? AND draw_id=? AND team_number=?
                  AND phase IN ('semifinal','final')
                GROUP BY attacker_tag
                """,
                (str(guild_id), int(draw_id), int(champion_team)),
            ) as cur:
                rows = [dict(x) for x in await cur.fetchall()]

        rows = [
            row for row in rows
            if normalize_tag(row["attacker_tag"]) in eligible
        ]
        for row in rows:
            row["attacker_name"] = eligible[normalize_tag(row["attacker_tag"])]

        rows.sort(
            key=lambda x: (
                -int(x["stars"] or 0),
                -float(x["destruction"] or 0),
                float(x["duration"] or 999999),
                x["attacker_name"] or "",
            )
        )
        return rows

    async def mvp_integrity_issues(
        self,
        guild_id: int,
        draw_id: int,
        champion_team: int,
    ) -> list[str]:
        members = await self.team_active_members(guild_id, draw_id, champion_team)
        eligible = {normalize_tag(row["player_tag"]) for row in members}
        issues = []
        if len(members) != 5 or len(eligible) != 5:
            issues.append(
                f"roster champion invalide : {len(eligible)}/5 joueurs uniques"
            )
            return issues

        for phase in ("semifinal", "final"):
            matches = [
                match
                for match in await self.get_phase_matches(guild_id, draw_id, phase)
                if champion_team in (int(match["team_a"]), int(match["team_b"]))
            ]
            label = self.phase_label(phase)
            if len(matches) != 1 or matches[0]["status"] != "completed":
                status = matches[0]["status"] if len(matches) == 1 else "introuvable"
                issues.append(f"{label} : match champion {status}")
                continue

            match = matches[0]
            lineup = await self.lineup_starters(int(match["id"]), champion_team)
            lineup_tags = {normalize_tag(row["player_tag"]) for row in lineup}
            if lineup_tags != eligible:
                issues.append(
                    f"{label} : lineup champion différente du roster de 5"
                )

            async with aiosqlite.connect(DATABASE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT attacker_tag,stars,destruction,duration
                    FROM cup_attacks
                    WHERE guild_id=? AND draw_id=? AND match_id=? AND team_number=?
                    ORDER BY id
                    """,
                    (
                        str(guild_id),int(draw_id),int(match["id"]),
                        int(champion_team),
                    ),
                ) as cur:
                    attacks = [dict(row) for row in await cur.fetchall()]

            counts = {tag: 0 for tag in eligible}
            outsiders = set()
            invalid = 0
            for attack in attacks:
                tag = normalize_tag(attack["attacker_tag"])
                if tag not in eligible:
                    outsiders.add(tag or "tag vide")
                    continue
                counts[tag] += 1
                stars = int(attack["stars"] or 0)
                destruction = float(attack["destruction"] or 0)
                duration = float(attack["duration"] or 0)
                if (
                    not 0 <= stars <= 3
                    or not math.isfinite(destruction)
                    or not 0 <= destruction <= 100
                    or not math.isfinite(duration)
                    or duration <= 0
                ):
                    invalid += 1
            if outsiders:
                issues.append(
                    f"{label} : attaquants hors roster {', '.join(sorted(outsiders))}"
                )
            if invalid:
                issues.append(f"{label} : {invalid} attaque(s) aux données invalides")

            count_values = set(counts.values())
            if len(count_values) != 1 or not count_values or next(iter(count_values)) == 0:
                details = ", ".join(
                    f"{tag}={count}" for tag, count in sorted(counts.items())
                )
                issues.append(
                    f"{label} : nombre d'attaques incomplet ou inégal ({details})"
                )
        return issues


    # ---------------------------------------------------------------------
    # COMMANDES /cup
    # ---------------------------------------------------------------------

    @app_commands.command(name="setup", description="Configurer la KAI CUP et son panneau d'inscription")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        salon="Salon où poster le panneau d'inscription",
        kai_clan_tag="Tag du clan Kai officiel",
        places_kai="Nombre de places prioritaires Kai",
        places_externes="Nombre de places externes",
    )
    async def cup_setup(
        self,
        interaction: discord.Interaction,
        salon: discord.TextChannel,
        kai_clan_tag: str,
        places_kai: app_commands.Range[int, 1, 100] = 60,
        places_externes: app_commands.Range[int, 1, 100] = 12,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.guild:
            return await interaction.followup.send(
                branded_text("❌ Configuration impossible hors serveur."),
                ephemeral=True,
            )

        async with self.registration_lock:
            if await self.registrations_frozen(interaction):
                return
            tag = normalize_tag(kai_clan_tag)
            clan = await self.api_get(f"/clans/{encode_tag(tag)}")
            if not clan:
                return await interaction.followup.send(
                    branded_text("❌ Impossible de vérifier ce clan avec l'API Clash.\n"
                    "Vérifie le tag et le token `COC_API_TOKEN`."),
                    ephemeral=True,
                )

            # Crée ou retrouve le rôle participant.
            role = discord.utils.get(interaction.guild.roles, name="KAI CUP • Participant")
            if not role:
                try:
                    role = await interaction.guild.create_role(
                        name="KAI CUP • Participant",
                        colour=EMBED_COLOR,
                        reason="Configuration KAI CUP",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    role = None

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    INSERT INTO cup_config(
                        guild_id,cup_name,kai_clan_tag,panel_channel_id,
                        panel_message_id,participant_role_id,registration_open,
                        internal_limit,external_limit,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        cup_name=excluded.cup_name,
                        kai_clan_tag=excluded.kai_clan_tag,
                        panel_channel_id=excluded.panel_channel_id,
                        participant_role_id=COALESCE(
                            excluded.participant_role_id,
                            cup_config.participant_role_id
                        ),
                        internal_limit=excluded.internal_limit,
                        external_limit=excluded.external_limit,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(interaction.guild.id),
                        CUP_NAME,
                        tag,
                        str(salon.id),
                        None,
                        str(role.id) if role else None,
                        0,
                        int(places_kai),
                        int(places_externes),
                        utc_now(),
                    ),
                )
                await db.commit()

            config = await self.get_config(interaction.guild.id)
            counts = await self.get_counts(interaction.guild.id)
            message = await salon.send(
                embed=self.build_registration_embed(config, counts),
                view=CupRegistrationPanel(self),
            )

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE cup_config
                    SET panel_message_id=?,updated_at=?
                    WHERE guild_id=?
                    """,
                    (str(message.id), utc_now(), str(interaction.guild.id)),
                )
                await db.commit()

            await self.audit(
                interaction.guild.id,
                interaction.user.id,
                "cup_setup",
                (
                    f"clan={clan.get('name')} {tag}; "
                    f"kai={places_kai}; external={places_externes}; "
                    f"channel={salon.id}"
                ),
            )

            await interaction.followup.send(
                branded_text("✅ **KAI CUP configurée.**\n"
                f"Clan Kai vérifié : **{clan.get('name')}** `{tag}`\n"
                f"Panneau créé dans {salon.mention}.\n\n"
                "Les inscriptions sont encore **fermées**. "
                "Utilise `/cup open` quand tu veux les ouvrir."),
                ephemeral=True,
            )

    @app_commands.command(name="open", description="Ouvrir les inscriptions KAI CUP")
    @app_commands.checks.has_permissions(administrator=True)
    async def cup_open(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        async with self.registration_lock:
            if await self.registrations_frozen(interaction):
                return
            config = await self.get_config(interaction.guild.id)
            if not config:
                return await interaction.followup.send(
                    branded_text("❌ Utilise d'abord `/cup setup`."),
                    ephemeral=True,
                )

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE cup_config
                    SET registration_open=1,updated_at=?
                    WHERE guild_id=?
                    """,
                    (utc_now(), str(interaction.guild.id)),
                )
                await db.commit()

            await self.audit(
                interaction.guild.id,
                interaction.user.id,
                "registrations_opened",
            )
            await self.update_panel(interaction.guild.id)
            await interaction.followup.send(
                branded_text("🟢 **Inscriptions KAI CUP ouvertes.**"),
                ephemeral=True,
            )

    @app_commands.command(name="close", description="Fermer les inscriptions et remplir les places libres")
    @app_commands.checks.has_permissions(administrator=True)
    async def cup_close(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        async with self.registration_lock:
            if await self.registrations_frozen(interaction):
                return
            config = await self.get_config(interaction.guild.id)
            if not config:
                return await interaction.followup.send(
                    branded_text("❌ KAI CUP non configurée."),
                    ephemeral=True,
                )

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE cup_config
                    SET registration_open=0,updated_at=?
                    WHERE guild_id=?
                    """,
                    (utc_now(), str(interaction.guild.id)),
                )
                await db.commit()

            # À la fermeture : les places Kai non utilisées deviennent accessibles
            # aux externes en liste d'attente, jusqu'à 72 joueurs actifs au total.
            counts = await self.get_counts(interaction.guild.id)
            total_capacity = int(config["internal_limit"]) + int(config["external_limit"])
            free_total = max(0, total_capacity - counts["total_active"])

            promoted_external: list[int] = []
            if free_total > 0 and counts["external_waiting"] > 0:
                promoted_external = await self.promote_waitlist(
                    interaction.guild,
                    "external",
                    min(free_total, counts["external_waiting"]),
                )

            await self.audit(
                interaction.guild.id,
                interaction.user.id,
                "registrations_closed",
                f"external_promoted={len(promoted_external)}",
            )
            await self.update_panel(interaction.guild.id)

            final_counts = await self.get_counts(interaction.guild.id)
            await interaction.followup.send(
                branded_text("🔒 **Inscriptions fermées.**\n"
                f"Participants validés : **{final_counts['total_active']}**\n"
                f"Promotions externes depuis la liste d'attente : "
                f"**{len(promoted_external)}**"),
                ephemeral=True,
            )

    @app_commands.command(name="me", description="Voir mon inscription KAI CUP")
    async def cup_me(self, interaction: discord.Interaction):
        await self.send_my_registration(interaction)

    @app_commands.command(name="unregister", description="Annuler mon inscription KAI CUP")
    async def cup_unregister(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        async with self.registration_lock:
            if await self.registrations_frozen(interaction):
                return
            row = await self.get_registration_by_discord(
                interaction.guild.id, interaction.user.id
            )
            if not row:
                return await interaction.followup.send(
                    branded_text("❌ Tu n'es pas inscrit."),
                    ephemeral=True,
                )

            participant_type = row["participant_type"]
            was_active = row["status"] == "active"

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "DELETE FROM cup_registrations WHERE id=?",
                    (row["id"],),
                )
                await db.commit()

            config = await self.get_config(interaction.guild.id)
            role = await self.get_participant_role(interaction.guild, config or {})
            if role and isinstance(interaction.user, discord.Member):
                try:
                    await interaction.user.remove_roles(
                        role,
                        reason="Désinscription KAI CUP",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            promoted = []
            if was_active:
                promoted = await self.promote_waitlist(
                    interaction.guild,
                    participant_type,
                    1,
                )

            await self.audit(
                interaction.guild.id,
                interaction.user.id,
                "registration_removed",
                f"{row['player_name']} {row['player_tag']}",
            )
            await self.update_panel(interaction.guild.id)

            extra = " Une personne de la liste d'attente a été promue." if promoted else ""
            await interaction.followup.send(
                branded_text(f"✅ Ton inscription a été annulée.{extra}"),
                ephemeral=True,
            )

    @app_commands.command(name="status", description="Voir l'état des inscriptions")
    async def cup_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("❌ Serveur requis."),
                ephemeral=True,
            )

        config = await self.get_config(interaction.guild.id)
        if not config:
            return await interaction.response.send_message(
                branded_text("❌ KAI CUP non configurée."),
                ephemeral=True,
            )

        counts = await self.get_counts(interaction.guild.id)
        embed = self.build_registration_embed(config, counts)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="list", description="Liste des inscrits KAI CUP")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT discord_id,player_name,player_tag,participant_type,status,
                       waitlist_position
                FROM cup_registrations
                WHERE guild_id=?
                ORDER BY
                    CASE WHEN status='active' THEN 0 ELSE 1 END,
                    participant_type,
                    registered_at
                """,
                (str(interaction.guild.id),),
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            return await interaction.followup.send(
                branded_text("Aucune inscription."),
                ephemeral=True,
            )

        # Messages limités : découpe en blocs.
        lines = []
        for row in rows:
            state = "✅" if row["status"] == "active" else f"⏳#{row['waitlist_position']}"
            category = "Kai" if row["participant_type"] == "kai" else "Ext"
            lines.append(
                f"{state} **{row['player_name']}** `{row['player_tag']}` "
                f"• {category} • <@{row['discord_id']}>"
            )

        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 3500:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks, start=1):
            embed = BrandedEmbed(
                title=f"🏆 Inscriptions KAI CUP • {i}/{len(chunks)}",
                description=chunk,
                color=EMBED_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="export", description="Exporter les inscriptions en CSV")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_export(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT *
                FROM cup_registrations
                WHERE guild_id=?
                ORDER BY status,participant_type,registered_at
                """,
                (str(interaction.guild.id),),
            ) as cur:
                rows = await cur.fetchall()

        if not rows:
            return await interaction.followup.send(
                branded_text("❌ Rien à exporter."),
                ephemeral=True,
            )

        text_buffer = io.StringIO()
        writer = csv.writer(text_buffer, delimiter=";")
        writer.writerow([
            "discord_id",
            "discord_name",
            "player_name",
            "player_tag",
            "townhall",
            "clan_name",
            "clan_tag",
            "type",
            "status",
            "waitlist_position",
            "declared_kai",
            "actual_kai",
            "registered_at",
        ])

        for row in rows:
            writer.writerow([
                row["discord_id"],
                row["discord_name"],
                row["player_name"],
                row["player_tag"],
                row["townhall_level"],
                row["clan_name"],
                row["clan_tag"],
                row["participant_type"],
                row["status"],
                row["waitlist_position"],
                row["declared_kai_member"],
                row["actual_kai_member"],
                row["registered_at"],
            ])

        data = text_buffer.getvalue().encode("utf-8-sig")
        file = discord.File(
            io.BytesIO(data),
            filename="kai_cup_inscriptions.csv",
        )

        await self.audit(
            interaction.guild.id,
            interaction.user.id,
            "registrations_exported",
            f"rows={len(rows)}",
        )
        await interaction.followup.send(
            content=branded_text(f"✅ Export de **{len(rows)}** inscription(s)."),
            file=file,
            ephemeral=True,
        )

    @app_commands.command(name="refresh", description="Rafraîchir le panneau d'inscription")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        await self.update_panel(interaction.guild.id)
        await interaction.followup.send(
            branded_text("✅ Panneau actualisé."),
            ephemeral=True,
        )


    @app_commands.command(name="draw", description="Tirer les Teams et créer leurs salons privés")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        test_mode="Test : autorise moins de 72 joueurs et des Teams incomplètes",
        salon_annonce="Optionnel : salon public où révéler les Teams",
        delai_reveal="Délai entre chaque révélation publique (secondes)",
    )
    async def cup_draw(
        self,
        interaction: discord.Interaction,
        test_mode: bool = False,
        salon_annonce: Optional[discord.TextChannel] = None,
        delai_reveal: app_commands.Range[int, 0, 10] = 0,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        guild = interaction.guild

        async with self.draw_lock, self.registration_lock:
            existing_draw = await self.registration_draw(guild.id)
            if existing_draw:
                if existing_draw["status"] in {"preparing", "error"}:
                    try:
                        await self.resume_draw_creation(guild, int(existing_draw["id"]))
                    except Exception as exc:
                        await self.audit(
                            guild.id,
                            interaction.user.id,
                            "draw_resume_error",
                            f"draw={existing_draw['draw_code']}; error={type(exc).__name__}: {exc}",
                        )
                        return await interaction.followup.send(
                            branded_text("❌ La reprise du tirage reste incomplète.\n"
                            f"**Code :** `{existing_draw['draw_code']}`\n"
                            f"Erreur : `{type(exc).__name__}: {exc}`\n"
                            "Corrige la permission ou le salon concerné puis relance `/cup draw`."),
                            ephemeral=True,
                        )
                    await self.audit(
                        guild.id,
                        interaction.user.id,
                        "draw_resumed",
                        f"draw={existing_draw['draw_code']}",
                    )
                    return await interaction.followup.send(
                        branded_text("✅ **TIRAGE REPRIS ET TERMINÉ**\n"
                        f"Code : `{existing_draw['draw_code']}`\n"
                        "La répartition enregistrée a été conservée ; seuls les éléments Discord manquants ont été repris."),
                        ephemeral=True,
                    )
                return await interaction.followup.send(
                    branded_text("❌ Un tirage actif existe déjà.\n"
                    f"Code : `{existing_draw['draw_code']}`\n"
                    "Pour un test, utilise `/cup reset_draw confirmation:RESET` avant de recommencer."),
                    ephemeral=True,
                )

            config = await self.get_config(guild.id)
            if not config:
                return await interaction.followup.send(
                    branded_text("❌ Lance d'abord `/cup setup`."),
                    ephemeral=True,
                )

            if not test_mode and bool(config["registration_open"]):
                return await interaction.followup.send(
                    branded_text("❌ Ferme d'abord les inscriptions avec `/cup close` avant le tirage officiel."),
                    ephemeral=True,
                )

            participants = await self.get_active_participants(guild.id)
            count = len(participants)
            total_capacity = int(config["internal_limit"]) + int(config["external_limit"])

            if not test_mode and count != total_capacity:
                return await interaction.followup.send(
                    branded_text(f"❌ Tirage officiel impossible : **{count}/{total_capacity}** joueurs validés.\n"
                    "Il faut le nombre complet de participants.\n"
                    "Pour tester le système avec les inscrits actuels, relance avec `test_mode:True`."),
                    ephemeral=True,
                )

            if test_mode and count < 1:
                return await interaction.followup.send(
                    branded_text("❌ Il faut au moins 1 inscription active pour tester."),
                    ephemeral=True,
                )

            me = guild.me
            if not me:
                return await interaction.followup.send(
                    branded_text("❌ Impossible de récupérer le membre du bot sur le serveur."),
                    ephemeral=True,
                )

            perms = me.guild_permissions
            missing = []
            if not perms.manage_channels:
                missing.append("Gérer les salons")
            if not perms.manage_roles:
                missing.append("Gérer les rôles")
            if missing:
                return await interaction.followup.send(
                    branded_text("❌ Permissions manquantes pour le bot : **"
                    + ", ".join(missing)
                    + "**."),
                    ephemeral=True,
                )

            seed = secrets.randbits(128)
            rng = random.Random(seed)
            rng.shuffle(participants)

            teams = [
                participants[i:i + 3]
                for i in range(0, len(participants), 3)
            ]
            team_count = len(teams)
            draw_code = secrets.token_hex(5).upper()
            now = utc_now()

            async with aiosqlite.connect(DATABASE) as db:
                cur = await db.execute(
                    """
                    INSERT INTO cup_draws(
                        guild_id,draw_code,seed,test_mode,
                        participant_count,team_count,status,
                        created_by,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(guild.id),
                        draw_code,
                        str(seed),
                        int(test_mode),
                        count,
                        team_count,
                        "preparing",
                        str(interaction.user.id),
                        now,
                    ),
                )
                draw_id = int(cur.lastrowid)
                await db.execute(
                    """
                    INSERT INTO cup_draw_plans(
                        draw_id,guild_id,announcement_channel_id,reveal_delay,
                        status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        draw_id,
                        str(guild.id),
                        str(salon_annonce.id) if salon_annonce else None,
                        int(delai_reveal),
                        "preparing",
                        now,
                        now,
                    ),
                )

                for team_number, members in enumerate(teams, start=1):
                    captain_id = int(members[0]["discord_id"])
                    await db.execute(
                        """
                        INSERT INTO cup_teams(
                            guild_id,draw_id,team_number,team_name,
                            captain_discord_id,status,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            str(guild.id),
                            draw_id,
                            team_number,
                            f"Team {team_number}",
                            str(captain_id),
                            "active",
                            now,
                        ),
                    )

                    for order, row in enumerate(members, start=1):
                        await db.execute(
                            """
                            INSERT INTO cup_team_members(
                                guild_id,draw_id,team_number,
                                discord_id,player_tag,player_name,
                                member_order,is_captain,joined_at
                            ) VALUES(?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                str(guild.id),
                                draw_id,
                                team_number,
                                str(row["discord_id"]),
                                row["player_tag"],
                                row["player_name"],
                                order,
                                int(order == 1),
                                now,
                            ),
                        )

                await db.commit()

            try:
                await self.resume_draw_creation(guild, draw_id)

            except Exception as exc:
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        "UPDATE cup_draws SET status='error' WHERE id=?",
                        (draw_id,),
                    )
                    await db.commit()

                await self.audit(
                    guild.id,
                    interaction.user.id,
                    "draw_error",
                    f"draw={draw_code}; error={type(exc).__name__}: {exc}",
                )
                return await interaction.followup.send(
                    branded_text("❌ Le tirage a été enregistré mais la création Discord a rencontré une erreur.\n"
                    f"**Code du tirage :** `{draw_code}`\n"
                    f"Erreur : `{type(exc).__name__}: {exc}`\n"
                    "Corrige le problème puis relance `/cup draw` : la même répartition sera reprise."),
                    ephemeral=True,
                )

            await self.audit(
                guild.id,
                interaction.user.id,
                "draw_created",
                (
                    f"draw={draw_code}; seed={seed}; test={test_mode}; "
                    f"participants={count}; teams={team_count}"
                ),
            )

            mode_text = "TEST" if test_mode else "OFFICIEL"
            await interaction.followup.send(
                branded_text(f"✅ **TIRAGE {mode_text} TERMINÉ**\n"
                f"Code : `{draw_code}`\n"
                f"Participants : **{count}**\n"
                f"Teams créées : **{team_count}**\n"
                f"Catégorie : **👥 KAI CUP — TEAMS**\n\n"
                "Le tirage est maintenant **verrouillé** : `/cup draw` refusera tout reroll."),
                ephemeral=True,
            )

    @app_commands.command(name="team", description="Voir ma Team KAI CUP")
    async def cup_team(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("❌ Serveur requis."),
                ephemeral=True,
            )

        team = await self.get_team_for_member(
            interaction.guild.id,
            interaction.user.id,
        )
        if not team:
            return await interaction.response.send_message(
                branded_text("❌ Tu n'es affecté à aucune Team active."),
                ephemeral=True,
            )

        members = await self.get_team_members(
            interaction.guild.id,
            int(team["draw_id"]),
            int(team["team_number"]),
        )
        await interaction.response.send_message(
            embed=self.build_team_embed(interaction.guild, team, members),
            ephemeral=True,
        )

    @app_commands.command(name="teams", description="Voir toutes les Teams KAI CUP")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_teams(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        draw = await self.get_active_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(
                branded_text("❌ Aucun tirage actif."),
                ephemeral=True,
            )

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT team_number,team_name,captain_discord_id,channel_id
                FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND status='active'
                ORDER BY team_number ASC
                """,
                (str(interaction.guild.id), int(draw["id"])),
            ) as cur:
                teams = [dict(row) for row in await cur.fetchall()]

        lines = []
        for team in teams:
            members = await self.get_team_members(
                interaction.guild.id,
                int(draw["id"]),
                int(team["team_number"]),
            )
            names = ", ".join(row["player_name"] for row in members)
            channel = f"<#{team['channel_id']}>" if team.get("channel_id") else "—"
            lines.append(
                f"**Team {team['team_number']}** • {names} • "
                f"Cap. <@{team['captain_discord_id']}> • {channel}"
            )

        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 3600:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks, start=1):
            embed = BrandedEmbed(
                title=f"🏆 Teams KAI CUP • {index}/{len(chunks)}",
                description=chunk,
                color=EMBED_COLOR,
            )
            embed.set_footer(
                text=f"Tirage {draw['draw_code']} • "
                     f"{'TEST' if draw['test_mode'] else 'OFFICIEL'}"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="draw_status", description="État du tirage KAI CUP")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_draw_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                branded_text("❌ Serveur requis."),
                ephemeral=True,
            )

        draw = await self.registration_draw(interaction.guild.id)
        if not draw:
            return await interaction.response.send_message(
                branded_text("ℹ️ Aucun tirage actif."),
                ephemeral=True,
            )

        embed = BrandedEmbed(
            title="🎲 Tirage KAI CUP",
            color=EMBED_COLOR,
        )
        embed.add_field(name="Code", value=f"`{draw['draw_code']}`", inline=True)
        embed.add_field(
            name="Mode",
            value="TEST" if draw["test_mode"] else "OFFICIEL",
            inline=True,
        )
        embed.add_field(
            name="Participants",
            value=str(draw["participant_count"]),
            inline=True,
        )
        embed.add_field(
            name="Teams",
            value=str(draw["team_count"]),
            inline=True,
        )
        status_labels = {
            "preparing": "Préparation Discord",
            "error": "Reprise nécessaire",
            "active": "Actif",
        }
        embed.add_field(
            name="État",
            value=status_labels.get(draw["status"], draw["status"]),
            inline=True,
        )
        embed.add_field(
            name="Créé le",
            value=draw["created_at"],
            inline=False,
        )
        embed.set_footer(text="Un tirage actif bloque tout nouveau /cup draw")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="set_captain", description="Changer le capitaine d'une Team")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_set_captain(
        self,
        interaction: discord.Interaction,
        team_number: app_commands.Range[int, 1, 24],
        membre: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        draw = await self.get_active_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(branded_text("❌ Aucun tirage actif."), ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id
                FROM cup_team_members
                WHERE guild_id=? AND draw_id=? AND team_number=? AND discord_id=?
                """,
                (
                    str(interaction.guild.id),
                    int(draw["id"]),
                    int(team_number),
                    str(membre.id),
                ),
            ) as cur:
                exists = await cur.fetchone()

            if not exists:
                return await interaction.followup.send(
                    branded_text(f"❌ {membre.mention} n'appartient pas à la Team {team_number}."),
                    ephemeral=True,
                )

            await db.execute(
                """
                UPDATE cup_team_members
                SET is_captain=0
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (str(interaction.guild.id), int(draw["id"]), int(team_number)),
            )
            await db.execute(
                """
                UPDATE cup_team_members
                SET is_captain=1
                WHERE guild_id=? AND draw_id=? AND team_number=? AND discord_id=?
                """,
                (
                    str(interaction.guild.id),
                    int(draw["id"]),
                    int(team_number),
                    str(membre.id),
                ),
            )
            await db.execute(
                """
                UPDATE cup_teams
                SET captain_discord_id=?
                WHERE guild_id=? AND draw_id=? AND team_number=?
                """,
                (
                    str(membre.id),
                    str(interaction.guild.id),
                    int(draw["id"]),
                    int(team_number),
                ),
            )
            await db.commit()

        await self.audit(
            interaction.guild.id,
            interaction.user.id,
            "captain_changed",
            f"team={team_number}; captain={membre.id}",
        )
        await interaction.followup.send(
            branded_text(f"✅ {membre.mention} est maintenant capitaine de la **Team {team_number}**."),
            ephemeral=True,
        )

    @app_commands.command(name="reset_draw", description="Supprimer un tirage TEST et ses espaces Discord")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        confirmation="Écris exactement RESET",
    )
    async def cup_reset_draw(
        self,
        interaction: discord.Interaction,
        confirmation: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        if confirmation.strip().upper() != "RESET":
            return await interaction.followup.send(
                branded_text("❌ Annulé. Pour confirmer, écris exactement `RESET`."),
                ephemeral=True,
            )

        draw = await self.registration_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(
                branded_text("ℹ️ Aucun tirage actif à supprimer."),
                ephemeral=True,
            )

        if not bool(draw["test_mode"]):
            return await interaction.followup.send(
                branded_text("❌ **Sécurité : le tirage actif est OFFICIEL.**\n"
                "Le reset automatique est volontairement bloqué pour empêcher un reroll."),
                ephemeral=True,
            )

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT role_id,channel_id
                FROM cup_teams
                WHERE guild_id=? AND draw_id=?
                """,
                (str(interaction.guild.id), int(draw["id"])),
            ) as cur:
                team_rows = [dict(row) for row in await cur.fetchall()]

        deleted_channels = 0
        deleted_roles = 0

        for row in team_rows:
            if row.get("channel_id"):
                channel = interaction.guild.get_channel(int(row["channel_id"]))
                if channel:
                    try:
                        await channel.delete(reason="Reset tirage TEST KAI CUP")
                        deleted_channels += 1
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        for row in team_rows:
            if row.get("role_id"):
                role = interaction.guild.get_role(int(row["role_id"]))
                if role:
                    try:
                        await role.delete(reason="Reset tirage TEST KAI CUP")
                        deleted_roles += 1
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                """
                UPDATE cup_draws
                SET status='reset',reset_by=?,reset_at=?
                WHERE id=?
                """,
                (str(interaction.user.id), utc_now(), int(draw["id"])),
            )
            await db.execute(
                "DELETE FROM cup_team_members WHERE guild_id=? AND draw_id=?",
                (str(interaction.guild.id), int(draw["id"])),
            )
            await db.execute(
                "DELETE FROM cup_teams WHERE guild_id=? AND draw_id=?",
                (str(interaction.guild.id), int(draw["id"])),
            )
            await db.execute(
                "DELETE FROM cup_draw_team_steps WHERE guild_id=? AND draw_id=?",
                (str(interaction.guild.id), int(draw["id"])),
            )
            await db.execute(
                "DELETE FROM cup_draw_plans WHERE draw_id=?",
                (int(draw["id"]),),
            )
            await db.commit()

        await self.audit(
            interaction.guild.id,
            interaction.user.id,
            "test_draw_reset",
            (
                f"draw={draw['draw_code']}; "
                f"channels={deleted_channels}; roles={deleted_roles}"
            ),
        )

        await interaction.followup.send(
            branded_text("✅ **Tirage TEST réinitialisé.**\n"
            f"Salons supprimés : **{deleted_channels}**\n"
            f"Rôles supprimés : **{deleted_roles}**\n"
            "Tu peux maintenant relancer `/cup draw test_mode:True`."),
            ephemeral=True,
        )



    @app_commands.command(name="event", description="Créer ou vérifier tout l'espace Discord KAI CUP")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="setup", value="setup"),
            app_commands.Choice(name="status", value="status"),
        ]
    )
    async def cup_event(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        if action.value == "setup":
            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.followup.send(
                    branded_text("Permission Gérer le serveur requise."),
                    ephemeral=True,
                )
            config = await self.ensure_event_space(interaction.guild)
            await interaction.followup.send(
                branded_text("✅ **Espace KAI CUP prêt.**\n"
                f"Live : <#{config['live_channel_id']}>\n"
                f"Résultats : <#{config['results_channel_id']}>\n"
                f"Bracket : <#{config['bracket_channel_id']}>\n"
                f"Control : <#{config['control_channel_id']}>"),
                ephemeral=True,
            )
        else:
            config = await self.get_event_config(interaction.guild.id)
            if not config:
                return await interaction.followup.send(
                    branded_text("Aucun espace événement configuré. Utilise `/cup event action:setup`."),
                    ephemeral=True,
                )
            await interaction.followup.send(
                branded_text(f"✅ Live <#{config['live_channel_id']}> • "
                f"Résultats <#{config['results_channel_id']}> • "
                f"Bracket <#{config['bracket_channel_id']}>"),
                ephemeral=True,
            )

    @app_commands.command(name="clan", description="Lier ou consulter le Clan Cup d'une Team")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="set", value="set"),
            app_commands.Choice(name="status", value="status"),
            app_commands.Choice(name="force", value="force"),
        ]
    )
    async def cup_clan(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        clan_tag: Optional[str] = None,
        team_number: Optional[app_commands.Range[int, 1, 24]] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        draw = await self.get_active_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(branded_text("Aucun tirage actif."), ephemeral=True)

        if action.value == "status":
            if team_number is None:
                own = await self.get_team_for_member(
                    interaction.guild.id, interaction.user.id
                )
                if not own:
                    return await interaction.followup.send(
                        branded_text("Indique `team_number` ou utilise cette commande en étant participant."),
                        ephemeral=True,
                    )
                team_number = int(own["team_number"])
            clan = await self.get_team_clan(
                interaction.guild.id, int(draw["id"]), int(team_number)
            )
            if not clan:
                return await interaction.followup.send(
                    branded_text(f"Team {team_number} : aucun Clan Cup lié."),
                    ephemeral=True,
                )
            return await interaction.followup.send(
                branded_text(f"Team {team_number} → **{clan['clan_name']}** `{clan['clan_tag']}`"),
                ephemeral=True,
            )

        if not clan_tag:
            return await interaction.followup.send(
                branded_text("Le paramètre `clan_tag` est requis."),
                ephemeral=True,
            )

        staff = interaction.user.guild_permissions.manage_guild
        if action.value == "force":
            if not staff:
                return await interaction.followup.send(
                    branded_text("Action réservée au staff."),
                    ephemeral=True,
                )
            if team_number is None:
                return await interaction.followup.send(
                    branded_text("`team_number` est requis avec force."),
                    ephemeral=True,
                )
        else:
            own = await self.get_team_for_member(
                interaction.guild.id, interaction.user.id
            )
            if not own:
                return await interaction.followup.send(
                    branded_text("Tu n'appartiens à aucune Team active."),
                    ephemeral=True,
                )
            team_number = int(own["team_number"])
            team_row = await self.get_team_row(
                interaction.guild.id, int(draw["id"]), int(team_number)
            )
            if int(team_row["captain_discord_id"]) != interaction.user.id and not staff:
                return await interaction.followup.send(
                    branded_text("Seul le capitaine ou le staff peut lier le Clan Cup."),
                    ephemeral=True,
                )
            existing = await self.get_team_clan(
                interaction.guild.id, int(draw["id"]), int(team_number)
            )
            if existing and existing["locked"]:
                return await interaction.followup.send(
                    branded_text("Le Clan Cup est déjà verrouillé. Le staff peut utiliser `action:force`."),
                    ephemeral=True,
                )

        tag = normalize_tag(clan_tag)
        clan = await self.api_get(f"/clans/{encode_tag(tag)}")
        if not clan:
            return await interaction.followup.send(
                branded_text("Clan introuvable via l'API Clash."),
                ephemeral=True,
            )

        try:
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    INSERT INTO cup_team_clans(
                        guild_id,draw_id,team_number,clan_tag,clan_name,
                        locked,set_by,set_at
                    ) VALUES(?,?,?,?,?,1,?,?)
                    ON CONFLICT(guild_id,draw_id,team_number) DO UPDATE SET
                        clan_tag=excluded.clan_tag,
                        clan_name=excluded.clan_name,
                        locked=1,
                        set_by=excluded.set_by,
                        set_at=excluded.set_at
                    """,
                    (
                        str(interaction.guild.id),
                        int(draw["id"]),
                        int(team_number),
                        tag,
                        clan.get("name", "Clan Cup"),
                        str(interaction.user.id),
                        utc_now(),
                    ),
                )
                await db.commit()
        except aiosqlite.IntegrityError:
            return await interaction.followup.send(
                branded_text("Ce clan est déjà utilisé par une autre Team."),
                ephemeral=True,
            )

        await self.audit(
            interaction.guild.id,
            interaction.user.id,
            "team_clan_set",
            f"team={team_number}; clan={tag}",
        )
        await interaction.followup.send(
            branded_text(f"✅ Team {team_number} → **{clan.get('name')}** `{tag}` verrouillé."),
            ephemeral=True,
        )

    @app_commands.command(name="checkin", description="Check-in du soir de la KAI CUP")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="in", value="in"),
            app_commands.Choice(name="status", value="status"),
            app_commands.Choice(name="reset", value="reset"),
        ]
    )
    async def cup_checkin(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        draw = await self.get_active_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(branded_text("Aucun tirage actif."), ephemeral=True)

        if action.value == "in":
            registration = await self.get_registration_by_discord(
                interaction.guild.id, interaction.user.id
            )
            if not registration or registration["status"] != "active":
                return await interaction.followup.send(
                    branded_text("Tu n'es pas participant actif."),
                    ephemeral=True,
                )
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO cup_checkins(
                        guild_id,draw_id,discord_id,checked_at
                    ) VALUES(?,?,?,?)
                    """,
                    (
                        str(interaction.guild.id),
                        int(draw["id"]),
                        str(interaction.user.id),
                        utc_now(),
                    ),
                )
                await db.commit()
            return await interaction.followup.send(
                branded_text("✅ Check-in validé. Tu es prêt pour la KAI CUP."),
                ephemeral=True,
            )

        if action.value == "reset":
            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.followup.send(
                    branded_text("Action réservée au staff."),
                    ephemeral=True,
                )
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "DELETE FROM cup_checkins WHERE guild_id=? AND draw_id=?",
                    (str(interaction.guild.id), int(draw["id"])),
                )
                await db.commit()
            return await interaction.followup.send(
                branded_text("✅ Check-ins remis à zéro."),
                ephemeral=True,
            )

        participants = await self.get_active_participants(interaction.guild.id)
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute(
                """
                SELECT discord_id
                FROM cup_checkins
                WHERE guild_id=? AND draw_id=?
                """,
                (str(interaction.guild.id), int(draw["id"])),
            ) as cur:
                checked = {str(x[0]) for x in await cur.fetchall()}
        missing = [x for x in participants if str(x["discord_id"]) not in checked]
        preview = "\n".join(
            f"• <@{x['discord_id']}> — {x['player_name']}"
            for x in missing[:30]
        )
        await interaction.followup.send(
            branded_text(f"✅ Présents : **{len(checked)}/{len(participants)}**\n"
            f"Absents : **{len(missing)}**\n\n"
            + (preview if preview else "Tout le monde est présent.")),
            ephemeral=True,
        )

    @app_commands.command(name="lineup", description="Déclarer les 3 titulaires + remplaçant du Round 2")
    async def cup_lineup(
        self,
        interaction: discord.Interaction,
        match_id: int,
        membre1: discord.Member,
        membre2: discord.Member,
        membre3: discord.Member,
        remplacant: discord.Member,
        team_number: Optional[app_commands.Range[int, 1, 24]] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        match = await self.get_match(match_id)
        if not match or int(match["guild_id"]) != interaction.guild.id:
            return await interaction.followup.send(branded_text("Match introuvable."), ephemeral=True)
        if match["phase"] != "round2":
            return await interaction.followup.send(
                branded_text("Cette commande est réservée au Round 2."),
                ephemeral=True,
            )

        staff = interaction.user.guild_permissions.manage_guild
        if team_number is None:
            own = await self.get_team_for_member(
                interaction.guild.id, interaction.user.id
            )
            if not own:
                return await interaction.followup.send(
                    branded_text("Indique la Team ou utilise la commande comme participant."),
                    ephemeral=True,
                )
            team_number = int(own["team_number"])

        if int(team_number) not in {int(match["team_a"]), int(match["team_b"])}:
            return await interaction.followup.send(
                branded_text("Cette Team ne joue pas ce match."),
                ephemeral=True,
            )

        team_row = await self.get_team_row(
            interaction.guild.id, int(match["draw_id"]), int(team_number)
        )
        if not staff and int(team_row["captain_discord_id"]) != interaction.user.id:
            return await interaction.followup.send(
                branded_text("Seul le capitaine ou le staff peut déclarer la lineup."),
                ephemeral=True,
            )

        selected = [membre1, membre2, membre3, remplacant]
        if len({m.id for m in selected}) != 4:
            return await interaction.followup.send(
                branded_text("Les 4 joueurs doivent être différents."),
                ephemeral=True,
            )

        team_members = await self.team_active_members(
            interaction.guild.id, int(match["draw_id"]), int(team_number)
        )
        by_discord = {int(x["discord_id"]): x for x in team_members}
        if any(m.id not in by_discord for m in selected):
            return await interaction.followup.send(
                branded_text("Au moins un joueur n'appartient pas à cette Team."),
                ephemeral=True,
            )

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                "DELETE FROM cup_lineups WHERE match_id=? AND team_number=?",
                (int(match_id), int(team_number)),
            )
            for slot, member in enumerate(selected, start=1):
                row = by_discord[member.id]
                await db.execute(
                    """
                    INSERT INTO cup_lineups(
                        guild_id,draw_id,match_id,team_number,
                        discord_id,player_tag,player_name,
                        slot,is_substitute,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(interaction.guild.id),
                        int(match["draw_id"]),
                        int(match_id),
                        int(team_number),
                        str(member.id),
                        row["player_tag"],
                        row["player_name"],
                        slot,
                        int(slot == 4),
                        utc_now(),
                    ),
                )
            await db.commit()

        refreshed = await self.get_match(match_id)
        refreshed = await self.sync_match_prerequisites(refreshed)
        await self.refresh_match_messages(interaction.guild, match_id)

        await interaction.followup.send(
            branded_text(f"✅ Lineup Team {team_number} enregistrée : "
            f"{membre1.mention}, {membre2.mention}, {membre3.mention} • "
            f"remplaçant {remplacant.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="advance", description="Créer automatiquement la prochaine phase du tournoi")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cup_advance(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        guild = interaction.guild
        draw = await self.get_active_draw(guild.id)
        if not draw:
            return await interaction.followup.send(branded_text("Aucun tirage actif."), ephemeral=True)

        async with self.advance_lock:
            try:
                await self.ensure_event_space(guild)
                draw_id = int(draw["id"])

                if await self.resume_phase_creation(guild, draw_id):
                    return await interaction.followup.send(
                        branded_text("✅ Création de la phase reprise et terminée. Les matchs enregistrés sont conservés."),
                        ephemeral=True,
                    )

                r1 = await self.get_phase_matches(guild.id, draw_id, "round1")
                if not r1:
                    teams = await self.active_team_numbers(guild.id, draw_id)
                    if len(teams) < 2 or len(teams) % 2 != 0:
                        return await interaction.followup.send(
                            branded_text(f"Impossible de lancer le Round 1 avec {len(teams)} Teams actives."),
                            ephemeral=True,
                        )
                    pairs = list(zip(teams[0::2], teams[1::2]))
                    ids = await self.create_phase_matches(guild, draw_id, "round1", pairs)
                    return await interaction.followup.send(
                        branded_text(f"✅ Round 1 créé : **{len(ids)} matchs**."),
                        ephemeral=True,
                    )

                if not await self.phase_completed(guild.id, draw_id, "round1"):
                    return await interaction.followup.send(
                        branded_text("Le Round 1 n'est pas terminé."),
                        ephemeral=True,
                    )
                if await self.unresolved_drafts_for_phase(guild.id, draw_id, "round1"):
                    return await interaction.followup.send(
                        branded_text("Des recrutements du Round 1 sont encore en attente."),
                        ephemeral=True,
                    )

                r2 = await self.get_phase_matches(guild.id, draw_id, "round2")
                if not r2:
                    winners = await self.phase_winners(guild.id, draw_id, "round1")
                    pairs = list(zip(winners[0::2], winners[1::2]))
                    ids = await self.create_phase_matches(guild, draw_id, "round2", pairs)
                    return await interaction.followup.send(
                        branded_text(f"✅ Round 2 créé : **{len(ids)} matchs**.\n"
                        "Les capitaines doivent maintenant déclarer 3 titulaires + 1 remplaçant."),
                        ephemeral=True,
                    )

                if not await self.phase_completed(guild.id, draw_id, "round2"):
                    return await interaction.followup.send(
                        branded_text("Le Round 2 n'est pas terminé."),
                        ephemeral=True,
                    )
                if await self.unresolved_drafts_for_phase(guild.id, draw_id, "round2"):
                    return await interaction.followup.send(
                        branded_text("Des recrutements du Round 2 sont encore en attente."),
                        ephemeral=True,
                    )

                playins = await self.get_phase_matches(guild.id, draw_id, "playin")
                if not playins:
                    winners = await self.phase_winners(guild.id, draw_id, "round2")
                    if len(winners) != 6:
                        return await interaction.followup.send(
                            branded_text(f"Il faut 6 Teams qualifiées, détectées : {len(winners)}."),
                            ephemeral=True,
                        )
                    finalist_issues = await self.roster_integrity_issues(
                        guild.id,
                        draw_id,
                        winners,
                        self.required_roster_size("playin"),
                    )
                    if finalist_issues:
                        raise RosterIntegrityError("phases finales", 5, finalist_issues)
                    await self.calculate_seeds(guild.id, draw_id, winners)
                    seeds = await self.seeds_map(guild.id, draw_id)
                    pairs = [(seeds[3], seeds[6]), (seeds[4], seeds[5])]
                    ids = await self.create_phase_matches(guild, draw_id, "playin", pairs)
                    return await interaction.followup.send(
                        branded_text(f"✅ Play-ins créés : **{len(ids)} matchs**. Seeds 1 et 2 sont en demi-finales."),
                        ephemeral=True,
                    )

                if not await self.phase_completed(guild.id, draw_id, "playin"):
                    return await interaction.followup.send(
                        branded_text("Les Play-ins ne sont pas terminés."),
                        ephemeral=True,
                    )

                semis = await self.get_phase_matches(guild.id, draw_id, "semifinal")
                if not semis:
                    seeds = await self.seeds_map(guild.id, draw_id)
                    playin_winners = await self.phase_winners(guild.id, draw_id, "playin")
                    if len(playin_winners) != 2:
                        return await interaction.followup.send(
                            branded_text("Impossible d'identifier les 2 vainqueurs des Play-ins."),
                            ephemeral=True,
                        )
                    # M1 = Seed3 vs Seed6 ; M2 = Seed4 vs Seed5
                    pairs = [
                        (seeds[1], playin_winners[1]),
                        (seeds[2], playin_winners[0]),
                    ]
                    ids = await self.create_phase_matches(
                        guild, draw_id, "semifinal", pairs
                    )
                    return await interaction.followup.send(
                        branded_text(f"✅ Demi-finales créées : **{len(ids)} matchs**."),
                        ephemeral=True,
                    )

                if not await self.phase_completed(guild.id, draw_id, "semifinal"):
                    return await interaction.followup.send(
                        branded_text("Les demi-finales ne sont pas terminées."),
                        ephemeral=True,
                    )

                final = await self.get_phase_matches(guild.id, draw_id, "final")
                if not final:
                    winners = await self.phase_winners(guild.id, draw_id, "semifinal")
                    ids = await self.create_phase_matches(
                        guild, draw_id, "final", [(winners[0], winners[1])]
                    )
                    return await interaction.followup.send(
                        branded_text("✅ **GRANDE FINALE créée.**"),
                        ephemeral=True,
                    )

                if await self.phase_completed(guild.id, draw_id, "final"):
                    state = await self.get_cup_state(guild.id, draw_id)
                    return await interaction.followup.send(
                        branded_text(f"🏆 Tournoi terminé. Champion : **Team {state.get('champion_team')}**."),
                        ephemeral=True,
                    )

                await interaction.followup.send(
                    branded_text("La Grande Finale est en cours."),
                    ephemeral=True,
                )
            except RosterIntegrityError as exc:
                await interaction.followup.send(
                    branded_text("❌ **Création bloquée : intégrité des rosters.**\n"
                    f"{exc}\n"
                    "Corrige les effectifs ou termine les recrutements, puis relance `/cup advance`. "
                    "Aucun nouveau match n'a été créé."),
                    ephemeral=True,
                )
            except SeedingIntegrityError as exc:
                await interaction.followup.send(
                    branded_text("❌ **Play-ins bloqués : classement non fiable.**\n"
                    f"{exc}\n"
                    "Complète ou départage les résultats concernés, puis relance `/cup advance`. "
                    "Les seeds existants et les matchs restent inchangés."),
                    ephemeral=True,
                )
            except Exception as exc:
                await interaction.followup.send(
                    branded_text("❌ Création de phase interrompue. Corrige les permissions ou la connexion, "
                    "puis relance `/cup advance` pour reprendre le plan enregistré. "
                    f"Détail : {type(exc).__name__}: {exc}"),
                    ephemeral=True,
                )

    @app_commands.command(name="bracket", description="Voir le bracket et l'avancement de la KAI CUP")
    async def cup_bracket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)
        await self.ensure_event_space(interaction.guild)
        await self.publish_bracket(interaction.guild)
        event = await self.get_event_config(interaction.guild.id)
        await interaction.followup.send(
            branded_text(f"✅ Bracket actualisé : <#{event['bracket_channel_id']}>"),
            ephemeral=True,
        )

    @app_commands.command(name="match", description="Statut, refresh, pénalité ou résultat manuel d'un match")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="status", value="status"),
            app_commands.Choice(name="refresh", value="refresh"),
            app_commands.Choice(name="penalty", value="penalty"),
            app_commands.Choice(name="result", value="result"),
        ]
    )
    async def cup_match(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        match_id: int,
        team_number: Optional[app_commands.Range[int, 1, 24]] = None,
        stars_adjustment: int = 0,
        reason: Optional[str] = None,
        stars_a: Optional[int] = None,
        destruction_a: Optional[float] = None,
        stars_b: Optional[int] = None,
        destruction_b: Optional[float] = None,
        duration_a: Optional[float] = None,
        duration_b: Optional[float] = None,
        winner_team: Optional[app_commands.Range[int, 1, 24]] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        async with self.match_lock:
            match = await self.get_match(match_id)
            if not match or int(match["guild_id"]) != interaction.guild.id:
                return await interaction.followup.send(branded_text("Match introuvable."), ephemeral=True)

            if action.value == "status":
                return await interaction.followup.send(
                    embed=await self.build_match_embed(match),
                    ephemeral=True,
                )

            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.followup.send(
                    branded_text("Action réservée au staff."),
                    ephemeral=True,
                )

            if await self.phase_is_preparing(match):
                return await interaction.followup.send(
                    branded_text("⏳ Phase en préparation : relance `/cup advance` avant de saisir un résultat."),
                    ephemeral=True,
                )

            if action.value == "refresh":
                try:
                    await self._process_match_live_locked(interaction.guild, match)
                except Exception as exc:
                    return await interaction.followup.send(
                        branded_text("⚠️ Reprise encore incomplète. Le résultat reste enregistré ; "
                        f"corrige le blocage puis réessaie. Détail : {type(exc).__name__}: {exc}"),
                        ephemeral=True,
                    )
                refreshed = await self.get_match(match_id)
                return await interaction.followup.send(
                    embed=await self.build_match_embed(refreshed),
                    ephemeral=True,
                )

            if match["status"] == "completed":
                return await interaction.followup.send(
                    branded_text("🔒 Match déjà terminé : résultat et pénalités verrouillés. "
                    "Une correction nécessite une reprise contrôlée du tableau par le staff."),
                    ephemeral=True,
                )

            if action.value == "penalty":
                if team_number not in {int(match["team_a"]), int(match["team_b"])}:
                    return await interaction.followup.send(
                        branded_text("`team_number` doit être l'une des deux Teams du match."),
                        ephemeral=True,
                    )
                async with aiosqlite.connect(DATABASE) as db:
                    await db.execute(
                        """
                        INSERT INTO cup_penalties(
                            guild_id,draw_id,match_id,team_number,
                            stars_adjustment,destruction_adjustment,
                            reason,actor_discord_id,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            str(interaction.guild.id),
                            int(match["draw_id"]),
                            int(match_id),
                            int(team_number),
                            int(stars_adjustment),
                            0.0,
                            reason or "Pénalité staff",
                            str(interaction.user.id),
                            utc_now(),
                        ),
                    )
                    await db.commit()
                await self.refresh_match_messages(interaction.guild, match_id)
                return await interaction.followup.send(
                    branded_text(f"✅ Ajustement **{stars_adjustment:+d} étoile(s)** appliqué à Team {team_number}."),
                    ephemeral=True,
                )

            # result manuel
            required = [stars_a, destruction_a, stars_b, destruction_b]
            if any(x is None for x in required):
                return await interaction.followup.send(
                    branded_text("Pour `result`, renseigne stars_a, destruction_a, stars_b et destruction_b."),
                    ephemeral=True,
                )

            max_stars = 3 * self.required_starters(match["phase"])
            if (
                not (0 <= stars_a <= max_stars and 0 <= stars_b <= max_stars)
                or not all(math.isfinite(v) and 0 <= v <= 100
                           for v in (destruction_a, destruction_b))
                or not all(math.isfinite(v) and v >= 0
                           for v in (duration_a or 0, duration_b or 0))
                or (winner_team is not None and winner_team not in
                    {int(match["team_a"]), int(match["team_b"])})
            ):
                return await interaction.followup.send(
                    branded_text(f"❌ Résultat invalide : étoiles entre 0 et {max_stars}, "
                    "destruction moyenne entre 0 et 100 %, durées positives ou nulles, "
                    "vainqueur parmi les deux équipes du match."),
                    ephemeral=True,
                )

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE cup_matches
                    SET team_a_stars=?,team_a_destruction=?,team_a_duration=?,
                        team_b_stars=?,team_b_destruction=?,team_b_duration=?,
                        status='live'
                    WHERE id=?
                    """,
                    (
                        int(stars_a),
                        float(destruction_a),
                        float(duration_a or 0),
                        int(stars_b),
                        float(destruction_b),
                        float(duration_b or 0),
                        int(match_id),
                    ),
                )
                await db.commit()

            refreshed = await self.get_match(match_id)
            try:
                await self.finalize_match(
                    interaction.guild,
                    refreshed,
                    forced_winner=int(winner_team) if winner_team else None,
                )
            except Exception as exc:
                return await interaction.followup.send(
                    branded_text("⚠️ Résultat enregistré et verrouillé, mais une suite Discord reste à reprendre. "
                    "Le bot réessaiera automatiquement ; le staff peut aussi utiliser "
                    f"`/cup match action:refresh match_id:{match_id}`. "
                    f"Détail : {type(exc).__name__}: {exc}"),
                    ephemeral=True,
                )
            await interaction.followup.send(
                branded_text("✅ Résultat manuel traité."),
                ephemeral=True,
            )

    @app_commands.command(name="draft", description="Ouvrir ou reprendre le recrutement d'un match gagné")
    async def cup_draft(
        self,
        interaction: discord.Interaction,
        match_id: int,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."), ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM cup_drafts WHERE match_id=?",
                (int(match_id),),
            ) as cur:
                draft = await cur.fetchone()

        if not draft or int(draft["guild_id"]) != interaction.guild.id:
            return await interaction.followup.send(
                branded_text("Aucun recrutement pour ce match."),
                ephemeral=True,
            )
        if draft["status"] != "pending":
            return await interaction.followup.send(
                branded_text(f"Recrutement terminé : `{draft['selected_player_tag'] or '—'}`."),
                ephemeral=True,
            )

        winner_row = await self.get_team_row(
            interaction.guild.id,
            int(draft["draw_id"]),
            int(draft["winner_team"]),
        )
        if not winner_row:
            return await interaction.followup.send(branded_text("Équipe gagnante introuvable."), ephemeral=True)
        staff = interaction.user.guild_permissions.manage_guild
        if int(winner_row["captain_discord_id"]) != interaction.user.id and not staff:
            return await interaction.followup.send(
                branded_text("Seul le capitaine gagnant ou le staff peut choisir."),
                ephemeral=True,
            )

        candidates = await self.draft_candidates(match_id)
        await interaction.followup.send(
            embed=BrandedEmbed(
                title="🎯 Recrutement",
                description=f"Choisis un joueur de la Team {draft['loser_team']}.",
                color=discord.Color.orange(),
            ),
            view=CupDraftView(self, match_id, candidates),
            ephemeral=True,
        )

    @app_commands.command(name="mvp", description="Classement MVP de la Team championne")
    async def cup_mvp(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not interaction.guild:
            return await interaction.followup.send(branded_text("Serveur requis."))

        draw = await self.get_active_draw(interaction.guild.id)
        if not draw:
            return await interaction.followup.send(branded_text("Aucun tirage actif."))

        state = await self.get_cup_state(interaction.guild.id, int(draw["id"]))
        champion = state.get("champion_team")
        if not champion:
            return await interaction.followup.send(
                branded_text("Le champion n'est pas encore connu.")
            )

        integrity_issues = await self.mvp_integrity_issues(
            interaction.guild.id,
            int(draw["id"]),
            int(champion),
        )
        if integrity_issues:
            details = "\n".join(f"• {issue}" for issue in integrity_issues[:10])
            return await interaction.followup.send(
                branded_text("❌ **Classement MVP indisponible : données incomplètes ou non éligibles.**\n"
                f"{details}\n"
                "Complète les données de demi-finale/finale avant toute décision sur le Pass d'Or.")
            )

        ranking = await self.mvp_ranking(
            interaction.guild.id, int(draw["id"]), int(champion)
        )
        if not ranking:
            return await interaction.followup.send(
                branded_text("Aucune attaque de demi-finale/finale enregistrée pour le MVP.")
            )

        lines = []
        for i, row in enumerate(ranking, start=1):
            lines.append(
                f"**{i}. {row['attacker_name']}** — "
                f"{int(row['stars'] or 0)}⭐ • "
                f"{float(row['destruction'] or 0):.0f}% • "
                f"{float(row['duration'] or 0):.0f}s cumulées • "
                f"{int(row['attacks'] or 0)} attaques"
            )

        top = ranking[0]
        tie = (
            len(ranking) > 1
            and int(top["stars"] or 0) == int(ranking[1]["stars"] or 0)
            and round(float(top["destruction"] or 0), 3)
            == round(float(ranking[1]["destruction"] or 0), 3)
            and round(float(top["duration"] or 0), 3)
            == round(float(ranking[1]["duration"] or 0), 3)
        )

        footer = (
            "Égalité parfaite en tête : aucun Pass d'Or attribué automatiquement ; départage staff requis."
            if tie
            else f"Leader statistique : {top['attacker_name']} • validation staff requise pour le Pass d'Or"
        )
        embed = BrandedEmbed(
            title=f"⭐ MVP DES CHAMPIONS • TEAM {champion}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed)


    async def discord_infrastructure_diagnostics(
        self,
        guild: discord.Guild,
        draw: Optional[dict],
    ) -> dict:
        """Contrôle Discord en lecture seule : aucune ressource n'est créée ou modifiée."""
        summary = {
            "event_expected": 8,
            "event_ok": 0,
            "event_missing": [],
            "event_inaccessible": [],
            "teams_total": 0,
            "team_roles_ok": 0,
            "team_channels_ok": 0,
            "team_missing_roles": [],
            "team_missing_channels": [],
            "match_channels_expected": 0,
            "match_channels_ok": 0,
            "match_missing_channels": [],
        }

        event = await self.get_event_config(guild.id)
        resources = (
            ("event_category_id", "catégorie publique", discord.CategoryChannel),
            ("matches_category_id", "catégorie matchs", discord.CategoryChannel),
            ("staff_category_id", "catégorie staff", discord.CategoryChannel),
            ("live_channel_id", "cup-live", discord.TextChannel),
            ("results_channel_id", "cup-resultats", discord.TextChannel),
            ("bracket_channel_id", "cup-bracket", discord.TextChannel),
            ("control_channel_id", "cup-control", discord.TextChannel),
            ("logs_channel_id", "cup-logs", discord.TextChannel),
        )
        for key, label, expected_type in resources:
            resource_id = event.get(key) if event else None
            resource = guild.get_channel(int(resource_id)) if resource_id else None
            if not isinstance(resource, expected_type):
                summary["event_missing"].append(label)
                continue
            summary["event_ok"] += 1
            bot_member = getattr(guild, "me", None)
            if bot_member and hasattr(resource, "permissions_for"):
                try:
                    permissions = resource.permissions_for(bot_member)
                    writable = not isinstance(resource, discord.TextChannel) or bool(
                        permissions.send_messages
                    )
                    if not permissions.view_channel or not writable:
                        summary["event_inaccessible"].append(label)
                except (AttributeError, TypeError):
                    summary["event_inaccessible"].append(label)

        if not draw:
            return summary

        draw_id = int(draw["id"])
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT team_number,role_id,channel_id
                FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND status='active'
                ORDER BY team_number
                """,
                (str(guild.id), draw_id),
            ) as cur:
                teams = [dict(row) for row in await cur.fetchall()]
            summary["teams_total"] = len(teams)
            for team in teams:
                team_number = int(team["team_number"])
                role_id = team.get("role_id")
                role = guild.get_role(int(role_id)) if role_id else None
                if isinstance(role, discord.Role):
                    summary["team_roles_ok"] += 1
                else:
                    summary["team_missing_roles"].append(team_number)

                channel_id = team.get("channel_id")
                channel = guild.get_channel(int(channel_id)) if channel_id else None
                if isinstance(channel, discord.TextChannel):
                    summary["team_channels_ok"] += 1
                else:
                    summary["team_missing_channels"].append(team_number)

            async with db.execute(
                """
                SELECT id,match_channel_id
                FROM cup_matches
                WHERE guild_id=? AND draw_id=? AND status!='completed'
                  AND match_channel_id IS NOT NULL
                ORDER BY id
                """,
                (str(guild.id), draw_id),
            ) as cur:
                matches = [dict(row) for row in await cur.fetchall()]
            summary["match_channels_expected"] = len(matches)
            for match in matches:
                channel = guild.get_channel(int(match["match_channel_id"]))
                if isinstance(channel, discord.TextChannel):
                    summary["match_channels_ok"] += 1
                else:
                    summary["match_missing_channels"].append(int(match["id"]))
        return summary

    async def operational_diagnostics(self, guild_id: int) -> dict:
        summary = {
            "draw": None,
            "teams_total": 0,
            "teams_ready": 0,
            "draw_journal_present": False,
            "missing_clan_teams": [],
            "participants_total": 0,
            "checkins": 0,
            "phase_plans_pending": 0,
            "match_blockers": {},
            "missing_lineups": [],
            "finalizations_pending": [],
            "finalizations_with_error": [],
            "drafts_pending": [],
            "drafts_expired": [],
        }
        draw = await self.registration_draw(guild_id)
        if not draw:
            async with aiosqlite.connect(DATABASE) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM cup_registrations WHERE guild_id=? AND status='active'",
                    (str(guild_id),),
                ) as cur:
                    summary["participants_total"] = int((await cur.fetchone())[0])
            return summary

        summary["draw"] = draw
        draw_id = int(draw["id"])
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(DATABASE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT status FROM cup_draw_plans WHERE draw_id=?",
                (draw_id,),
            ) as cur:
                plan = await cur.fetchone()
            summary["draw_journal_present"] = bool(plan)

            async with db.execute(
                """
                SELECT team_number,role_id,channel_id
                FROM cup_teams
                WHERE guild_id=? AND draw_id=? AND status='active'
                ORDER BY team_number
                """,
                (str(guild_id), draw_id),
            ) as cur:
                teams = [dict(row) for row in await cur.fetchall()]
            summary["teams_total"] = len(teams)

            if plan:
                async with db.execute(
                    """
                    SELECT COUNT(*)
                    FROM cup_draw_team_steps s
                    JOIN cup_teams t
                      ON t.guild_id=s.guild_id
                     AND t.draw_id=s.draw_id
                     AND t.team_number=s.team_number
                    WHERE s.guild_id=? AND s.draw_id=? AND s.status='ready'
                      AND t.status='active'
                    """,
                    (str(guild_id), draw_id),
                ) as cur:
                    summary["teams_ready"] = int((await cur.fetchone())[0])
            else:
                summary["teams_ready"] = sum(
                    1 for team in teams if team.get("role_id") and team.get("channel_id")
                )

            async with db.execute(
                """
                SELECT team_number FROM cup_team_clans
                WHERE guild_id=? AND draw_id=?
                """,
                (str(guild_id), draw_id),
            ) as cur:
                clan_teams = {int(row[0]) for row in await cur.fetchall()}
            summary["missing_clan_teams"] = [
                int(team["team_number"])
                for team in teams
                if int(team["team_number"]) not in clan_teams
            ]

            async with db.execute(
                """
                SELECT COUNT(DISTINCT discord_id)
                FROM cup_team_members
                WHERE guild_id=? AND draw_id=?
                """,
                (str(guild_id), draw_id),
            ) as cur:
                summary["participants_total"] = int((await cur.fetchone())[0])
            async with db.execute(
                """
                SELECT COUNT(DISTINCT discord_id)
                FROM cup_checkins
                WHERE guild_id=? AND draw_id=?
                """,
                (str(guild_id), draw_id),
            ) as cur:
                summary["checkins"] = int((await cur.fetchone())[0])

            async with db.execute(
                """
                SELECT COUNT(*) FROM cup_phase_plans
                WHERE guild_id=? AND draw_id=? AND status='preparing'
                """,
                (str(guild_id), draw_id),
            ) as cur:
                summary["phase_plans_pending"] = int((await cur.fetchone())[0])

            async with db.execute(
                """
                SELECT status,COUNT(*) FROM cup_matches
                WHERE guild_id=? AND draw_id=? AND status!='completed'
                GROUP BY status
                """,
                (str(guild_id), draw_id),
            ) as cur:
                summary["match_blockers"] = {
                    str(row[0]): int(row[1]) for row in await cur.fetchall()
                }

            async with db.execute(
                """
                SELECT m.id,m.team_a,m.team_b,l.team_number,COUNT(l.id) AS selected
                FROM cup_matches m
                LEFT JOIN cup_lineups l ON l.match_id=m.id
                WHERE m.guild_id=? AND m.draw_id=? AND m.phase='round2'
                  AND m.status NOT IN ('completed','tiebreak')
                GROUP BY m.id,m.team_a,m.team_b,l.team_number
                ORDER BY m.id,l.team_number
                """,
                (str(guild_id), draw_id),
            ) as cur:
                lineup_rows = [dict(row) for row in await cur.fetchall()]
            by_match = {}
            for row in lineup_rows:
                info = by_match.setdefault(
                    int(row["id"]),
                    {
                        "teams": (int(row["team_a"]), int(row["team_b"])),
                        "counts": {},
                    },
                )
                if row["team_number"] is not None:
                    info["counts"][int(row["team_number"])] = int(row["selected"])
            for match_id, info in by_match.items():
                for team_number in info["teams"]:
                    if info["counts"].get(team_number, 0) != 4:
                        summary["missing_lineups"].append((match_id, team_number))

            async with db.execute(
                """
                SELECT match_id,last_error FROM cup_match_finalizations
                WHERE guild_id=? AND draw_id=? AND status='pending'
                ORDER BY match_id
                """,
                (str(guild_id), draw_id),
            ) as cur:
                for row in await cur.fetchall():
                    match_id = int(row[0])
                    summary["finalizations_pending"].append(match_id)
                    if row[1]:
                        summary["finalizations_with_error"].append(match_id)

            async with db.execute(
                """
                SELECT match_id,deadline FROM cup_drafts
                WHERE guild_id=? AND draw_id=? AND status='pending'
                ORDER BY match_id
                """,
                (str(guild_id), draw_id),
            ) as cur:
                for row in await cur.fetchall():
                    match_id = int(row[0])
                    summary["drafts_pending"].append(match_id)
                    try:
                        if datetime.fromisoformat(str(row[1])) <= now:
                            summary["drafts_expired"].append(match_id)
                    except (TypeError, ValueError):
                        summary["drafts_expired"].append(match_id)
        return summary

    @staticmethod
    def diagnostics_action(
        summary: dict,
        infrastructure: Optional[dict] = None,
    ) -> str:
        draw = summary.get("draw")
        if not draw:
            return "Ouvrir/compléter les inscriptions, puis lancer le tirage."
        if draw["status"] in {"preparing", "error"}:
            return "Corriger la dernière erreur puis relancer `/cup draw` pour reprendre le tirage."
        if summary["phase_plans_pending"]:
            return "Relancer `/cup advance` pour terminer la création de phase."
        if summary["finalizations_with_error"]:
            ids = ", ".join(map(str, summary["finalizations_with_error"][:5]))
            return f"Reprendre les matchs {ids} avec `/cup match action:refresh`."
        if summary["drafts_expired"]:
            ids = ", ".join(map(str, summary["drafts_expired"][:5]))
            return f"Rouvrir les recrutements expirés {ids} avec `/cup draft`."
        infrastructure = infrastructure or {}
        if infrastructure.get("event_missing") or infrastructure.get("event_inaccessible"):
            return "Réparer l'espace événement avec `/cup event action:setup`, puis relancer le diagnostic."
        if infrastructure.get("team_missing_roles") or infrastructure.get("team_missing_channels"):
            teams = sorted(set(
                infrastructure.get("team_missing_roles", [])
                + infrastructure.get("team_missing_channels", [])
            ))
            preview = ", ".join(map(str, teams[:10]))
            return f"Restaurer les rôles/salons Discord des Teams {preview} avant de poursuivre."
        if infrastructure.get("match_missing_channels"):
            ids = ", ".join(map(str, infrastructure["match_missing_channels"][:5]))
            return f"Reprendre les salons des matchs {ids} avec `/cup match action:refresh`."
        if summary["missing_clan_teams"]:
            teams = ", ".join(map(str, summary["missing_clan_teams"][:10]))
            return f"Lier les Clan Cup manquants des Teams {teams} avec `/cup clan`."
        if summary["missing_lineups"]:
            match_id, team_number = summary["missing_lineups"][0]
            return f"Déclarer la lineup de la Team {team_number} pour le match {match_id}."
        missing_checkins = summary["participants_total"] - summary["checkins"]
        if missing_checkins > 0:
            return "Contrôler les absents avec `/cup checkin action:status`."
        if summary["finalizations_pending"] or summary["drafts_pending"]:
            return "Surveiller les reprises automatiques encore en attente."
        return "Aucun blocage enregistré : contrôle Discord réel recommandé avant lancement."


    @app_commands.command(name="diagnostics", description="Tester la connexion API KAI CUP")
    @app_commands.checks.has_permissions(administrator=True)
    async def cup_diagnostics(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send(branded_text("❌ Serveur requis."), ephemeral=True)

        config = await self.get_config(interaction.guild.id)
        if not config:
            return await interaction.followup.send(
                branded_text("❌ KAI CUP non configurée."),
                ephemeral=True,
            )

        clan_tag = config.get("kai_clan_tag")
        clan = await self.api_get(f"/clans/{encode_tag(clan_tag)}") if clan_tag else None
        operations = await self.operational_diagnostics(interaction.guild.id)
        draw = operations["draw"]
        infrastructure = await self.discord_infrastructure_diagnostics(
            interaction.guild,
            draw,
        )
        infrastructure_blocking = bool(
            infrastructure["event_missing"]
            or infrastructure["event_inaccessible"]
            or infrastructure["team_missing_roles"]
            or infrastructure["team_missing_channels"]
            or infrastructure["match_missing_channels"]
        )
        blocking = bool(
            not clan
            or infrastructure_blocking
            or (draw and draw["status"] in {"preparing", "error"})
            or operations["phase_plans_pending"]
            or operations["missing_clan_teams"]
            or operations["missing_lineups"]
            or operations["finalizations_pending"]
            or operations["drafts_expired"]
            or operations["participants_total"] != operations["checkins"]
        )

        embed = BrandedEmbed(
            title="🧪 Diagnostics KAI CUP • Staff",
            color=discord.Color.red() if blocking else discord.Color.green(),
        )
        embed.add_field(
            name="COC_API_TOKEN",
            value="✅ Présent" if self.api_token else "❌ Absent",
            inline=False,
        )
        embed.add_field(
            name="Clan Kai",
            value=(
                f"✅ {clan.get('name')} `{clan.get('tag')}`"
                if clan
                else f"❌ Inaccessible `{clan_tag or 'non configuré'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Base SQLite",
            value=f"✅ `{DATABASE}`",
            inline=False,
        )
        embed.add_field(
            name="Moteur KAI CUP",
            value="✅ Inscriptions • Teams • Clan Cup • Live • Draft • Playoffs • MVP",
            inline=False,
        )
        infrastructure_issues = (
            infrastructure["event_missing"]
            + infrastructure["event_inaccessible"]
        )
        team_issues = sorted(set(
            infrastructure["team_missing_roles"]
            + infrastructure["team_missing_channels"]
        ))
        match_issues = infrastructure["match_missing_channels"]
        embed.add_field(
            name="Infrastructure Discord (lecture seule)",
            value=(
                f"Espace événement : **{infrastructure['event_ok']}/{infrastructure['event_expected']}**"
                + (f" • anomalies : {', '.join(infrastructure_issues[:8])}" if infrastructure_issues else "")
                + f"\nRôles Teams : **{infrastructure['team_roles_ok']}/{infrastructure['teams_total']}** • "
                f"salons Teams : **{infrastructure['team_channels_ok']}/{infrastructure['teams_total']}**"
                + (f" • anomalies T{', T'.join(map(str, team_issues[:12]))}" if team_issues else "")
                + f"\nSalons de match enregistrés : **{infrastructure['match_channels_ok']}/{infrastructure['match_channels_expected']}**"
                + (f" • absents M{', M'.join(map(str, match_issues[:8]))}" if match_issues else "")
            ),
            inline=False,
        )
        if not draw:
            embed.add_field(
                name="Tournoi",
                value=(
                    "ℹ️ Tirage non lancé\n"
                    f"Inscriptions actives : **{operations['participants_total']}**"
                ),
                inline=False,
            )
        else:
            status_label = {
                "preparing": "🟠 préparation",
                "error": "🔴 erreur",
                "active": "🟢 actif",
            }.get(draw["status"], draw["status"])
            missing_clans = operations["missing_clan_teams"]
            embed.add_field(
                name="Tirage et équipes",
                value=(
                    f"État : **{status_label}** • code `{draw['draw_code']}`\n"
                    f"QG prêts : **{operations['teams_ready']}/{operations['teams_total']}**\n"
                    f"Clan Cup liés : **{operations['teams_total'] - len(missing_clans)}/{operations['teams_total']}**"
                    + (f" • manquants T{', T'.join(map(str, missing_clans[:12]))}" if missing_clans else "")
                ),
                inline=False,
            )
            missing_checkins = max(
                0,
                operations["participants_total"] - operations["checkins"],
            )
            embed.add_field(
                name="Check-in",
                value=(
                    f"Présents : **{operations['checkins']}/{operations['participants_total']}** • "
                    f"absents : **{missing_checkins}**"
                ),
                inline=False,
            )
            blockers = operations["match_blockers"]
            lineup_preview = ", ".join(
                f"M{match_id}/T{team_number}"
                for match_id, team_number in operations["missing_lineups"][:8]
            )
            embed.add_field(
                name="Matchs et compositions",
                value=(
                    f"Phases à reprendre : **{operations['phase_plans_pending']}**\n"
                    f"Clans attendus : **{blockers.get('waiting_clan', 0)}** • "
                    f"lineups attendues : **{blockers.get('waiting_lineup', 0)}** • "
                    f"rosters invalides : **{blockers.get('roster_invalid', 0)}** • "
                    f"égalités 1v1 : **{blockers.get('tiebreak', 0)}**"
                    + (f"\nLineups incomplètes : `{lineup_preview}`" if lineup_preview else "")
                ),
                inline=False,
            )
            embed.add_field(
                name="Reprises automatiques",
                value=(
                    f"Finalisations : **{len(operations['finalizations_pending'])}** "
                    f"(erreurs : **{len(operations['finalizations_with_error'])}**)\n"
                    f"Recrutements : **{len(operations['drafts_pending'])}** "
                    f"(expirés : **{len(operations['drafts_expired'])}**)"
                ),
                inline=False,
            )
        embed.add_field(
            name="Action staff prioritaire",
            value=self.diagnostics_action(operations, infrastructure),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(KaiCup(bot))
