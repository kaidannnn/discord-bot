"""Tests hors ligne : SQLite temporaire, aucune connexion Discord/Clash."""
import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import aiosqlite
import discord
from discord import app_commands

spec = importlib.util.spec_from_file_location('cup_under_test', Path(__file__).parents[1]/'cogs/cup.py')
cup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cup
spec.loader.exec_module(cup)

class CupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cup.DATABASE = str(Path(self.tmp.name)/'test.db')
        self.cog = cup.KaiCup(NS(guilds=[]))
        await self.cog.setup_database()
        self.team_channels = {}
        for t in range(1,25):
            channel = Mock(spec=discord.TextChannel)
            channel.id = 700 + t
            channel.send = AsyncMock(return_value=NS(id=1700 + t))
            self.team_channels[channel.id] = channel
        self.guild = NS(
            id=1,
            get_channel=lambda channel_id: self.team_channels.get(channel_id),
            get_member=lambda _: None,
            get_role=lambda _: None,
        )
        for name in ['refresh_match_messages','publish_bracket','cup_log','ensure_event_space','announce_champion','announce_match_result']:
            setattr(self.cog,name,AsyncMock())
        self.cog.create_match_channel = AsyncMock(return_value=(NS(id=800),NS(id=900),NS(id=1000)))
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute("INSERT INTO cup_draws(id,guild_id,draw_code,seed,participant_count,team_count,created_at) VALUES(1,'1','TEST','1',72,24,?)",(cup.utc_now(),))
            for t in range(1,25):
                await db.execute("INSERT INTO cup_teams(guild_id,draw_id,team_number,team_name,captain_discord_id,channel_id,created_at) VALUES('1',1,?,?,?,?,?)",(t,f'Team {t}',str(t*10),str(700+t),cup.utc_now()))
                await db.execute("INSERT INTO cup_team_clans(guild_id,draw_id,team_number,clan_tag,clan_name,set_at) VALUES('1',1,?,?,?,?)",(t,f'#C{t}',f'Clan {t}',cup.utc_now()))
                for j in range(3):
                    await db.execute("INSERT INTO cup_team_members(guild_id,draw_id,team_number,discord_id,player_tag,player_name,member_order,joined_at) VALUES('1',1,?,?,?,?,?,?)",(t,str(t*10+j),f'#P{t}X{j}',f'Joueur {t}-{j}',j+1,cup.utc_now()))
            await db.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def interaction(self, guild=None):
        return NS(guild=guild or self.guild,user=NS(id=10,guild_permissions=NS(manage_guild=True)),response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))

    async def first_match(self):
        ids=await self.cog.create_phase_matches(self.guild,1,'round1',[(1,2)])
        return await self.cog.get_match(ids[0])

    async def manual(self, match_id, action='result', **kwargs):
        i=self.interaction()
        args=dict(
            stars_a=9,destruction_a=100,duration_a=100+match_id,
            stars_b=8,destruction_b=90,duration_b=200+match_id,
        )
        args.update(kwargs)
        await self.cog.cup_match.callback(self.cog,i,app_commands.Choice(name=action,value=action),match_id,**args)
        return i

    async def test_completed_result_cannot_be_reopened(self):
        m=await self.first_match()
        await self.manual(m['id'])
        await self.manual(m['id'],stars_a=0,stars_b=9)
        m=await self.cog.get_match(m['id'])
        self.assertEqual((m['winner_team'],m['team_a_stars']),(1,9))

    async def test_completed_penalty_rejected(self):
        m=await self.first_match(); await self.manual(m['id'])
        await self.manual(m['id'],action='penalty',team_number=1,stars_adjustment=-3)
        self.assertEqual(await self.cog.get_penalties(m['id']),{})

    async def test_invalid_manual_results_rejected(self):
        m=await self.first_match()
        for values in [dict(stars_a=-1),dict(stars_a=10),dict(destruction_a=300),dict(destruction_a=float('nan')),dict(duration_b=-2),dict(winner_team=3)]:
            with self.subTest(values=values):
                await self.manual(m['id'],**values)
                self.assertNotEqual((await self.cog.get_match(m['id']))['status'],'completed')
        self.assertEqual(await self.cog.draft_candidates(m['id']),[])

    async def test_draft_reopening_preserves_selection(self):
        m=await self.first_match(); await self.manual(m['id'])
        self.assertTrue(await self.cog.execute_draft(self.guild,m['id'],'#P2X0',automatic=True))
        await self.cog.open_draft(self.guild,await self.cog.get_match(m['id']))
        self.assertFalse(await self.cog.execute_draft(self.guild,m['id'],'#P2X1',automatic=True))
        self.assertEqual(len(await self.cog.team_active_members(1,1,1)),4)

    async def test_pending_draft_deadline_preserved(self):
        m=await self.first_match(); await self.manual(m['id'])
        async with aiosqlite.connect(cup.DATABASE) as db:
            before=await (await db.execute('SELECT deadline FROM cup_drafts')).fetchone()
        await self.cog.open_draft(self.guild,await self.cog.get_match(m['id']))
        async with aiosqlite.connect(cup.DATABASE) as db:
            after=await (await db.execute('SELECT deadline FROM cup_drafts')).fetchone()
        self.assertEqual(before,after)

    async def test_foreign_guild_draft_rejected(self):
        m=await self.first_match(); await self.manual(m['id'])
        other=NS(id=2)
        self.assertFalse(await self.cog.execute_draft(other,m['id'],'#P2X0',automatic=True))
        i=self.interaction(other)
        await self.cog.cup_draft.callback(self.cog,i,m['id'])
        self.assertIn('Aucun recrutement',i.followup.send.call_args.args[0])

    async def test_concurrent_manual_results_have_one_winner(self):
        m=await self.first_match()
        await asyncio.gather(self.manual(m['id']),self.manual(m['id'],stars_a=0,stars_b=9))
        m=await self.cog.get_match(m['id'])
        async with aiosqlite.connect(cup.DATABASE) as db:
            drafts=await (await db.execute('SELECT winner_team FROM cup_drafts')).fetchall()
        self.assertEqual(drafts,[(m['winner_team'],)])
        self.assertEqual(self.cog.refresh_match_messages.await_count,1)

    async def test_refresh_command_does_not_deadlock(self):
        m=await self.first_match()
        self.cog.current_war=AsyncMock(return_value=None)
        await asyncio.wait_for(self.manual(m['id'],action='refresh'),2)

    async def test_stale_live_snapshot_cannot_overwrite_result(self):
        m=await self.first_match(); await self.manual(m['id'])
        self.cog.current_war=AsyncMock()
        await self.cog.process_match_live(self.guild,m)
        self.cog.current_war.assert_not_awaited()

    async def test_tie_requires_staff_tiebreak(self):
        m=await self.first_match()
        await self.manual(
            m['id'],stars_b=9,destruction_b=100,duration_a=100,duration_b=100
        )
        m=await self.cog.get_match(m['id'])
        self.assertEqual(m['status'],'tiebreak')
        self.assertIsNone(m['winner_team'])
        await self.manual(
            m['id'],stars_b=9,destruction_b=100,duration_a=100,duration_b=100,
            winner_team=2,
        )
        self.assertEqual((await self.cog.get_match(m['id']))['winner_team'],2)

    async def add_complete_mvp_data(self, *, tie=False):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            for order in (4,5):
                await db.execute(
                    """
                    INSERT INTO cup_team_members(
                        guild_id,draw_id,team_number,discord_id,player_tag,
                        player_name,member_order,joined_at
                    ) VALUES('1',1,1,?,?,?,?,?)
                    """,
                    (str(100+order),f'#P1X{order-1}',f'Joueur 1-{order-1}',order,now),
                )
            match_ids=[]
            for phase,opponent in [('semifinal',2),('final',3)]:
                cur=await db.execute(
                    """
                    INSERT INTO cup_matches(
                        guild_id,draw_id,phase,match_order,team_a,team_b,status,
                        winner_team,loser_team,created_at
                    ) VALUES('1',1,?,1,1,?,'completed',1,?,?)
                    """,
                    (phase,opponent,opponent,now),
                )
                match_id=int(cur.lastrowid)
                match_ids.append(match_id)
                for index in range(5):
                    tag=f'#P1X{index}'
                    name=f'Joueur 1-{index}'
                    await db.execute(
                        """
                        INSERT INTO cup_lineups(
                            guild_id,draw_id,match_id,team_number,discord_id,
                            player_tag,player_name,slot,is_substitute,created_at
                        ) VALUES('1',1,?,1,?,?,?,?,0,?)
                        """,
                        (match_id,str(10+index),tag,name,index+1,now),
                    )
                    stars=3 if tie and index < 2 else max(0,3-index)
                    destruction=100 if tie and index < 2 else 100-index
                    duration=100 if tie and index < 2 else 100+index
                    await db.execute(
                        """
                        INSERT INTO cup_attacks(
                            guild_id,draw_id,match_id,phase,team_number,
                            attacker_tag,attacker_name,defender_tag,defender_name,
                            attack_order,stars,destruction,duration,created_at
                        ) VALUES('1',1,?,?,1,?,?,?,'Défenseur',1,?,?,?,?)
                        """,
                        (
                            match_id,phase,tag,name,f'#D{match_id}X{index}',
                            stars,destruction,duration,now,
                        ),
                    )
            await db.execute(
                """
                INSERT INTO cup_state(guild_id,draw_id,current_phase,status,champion_team,updated_at)
                VALUES('1',1,'final','completed',1,?)
                ON CONFLICT(guild_id,draw_id) DO UPDATE SET
                    current_phase='final',status='completed',champion_team=1,updated_at=excluded.updated_at
                """,
                (now,),
            )
            await db.commit()
        return match_ids

    async def test_mvp_requires_equal_complete_attacks_for_five_champions(self):
        match_ids=await self.add_complete_mvp_data()
        self.assertEqual(await self.cog.mvp_integrity_issues(1,1,1),[])
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                "DELETE FROM cup_attacks WHERE match_id=? AND attacker_tag='#P1X4'",
                (match_ids[0],),
            )
            await db.commit()
        issues=await self.cog.mvp_integrity_issues(1,1,1)
        self.assertTrue(any("incomplet ou inégal" in issue for issue in issues))

    async def test_mvp_rejects_outsider_and_filters_ranking(self):
        match_ids=await self.add_complete_mvp_data()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                """
                INSERT INTO cup_attacks(
                    guild_id,draw_id,match_id,phase,team_number,attacker_tag,
                    attacker_name,defender_tag,attack_order,stars,destruction,
                    duration,created_at
                ) VALUES('1',1,?,'semifinal',1,'#OUT','Intrus','#DOUT',1,3,100,90,?)
                """,
                (match_ids[0],cup.utc_now()),
            )
            await db.commit()
        issues=await self.cog.mvp_integrity_issues(1,1,1)
        self.assertTrue(any("hors roster" in issue for issue in issues))
        ranking=await self.cog.mvp_ranking(1,1,1)
        self.assertEqual(len(ranking),5)
        self.assertNotIn('#OUT',{row['attacker_tag'] for row in ranking})

    async def test_mvp_perfect_tie_never_assigns_pass_automatically(self):
        await self.add_complete_mvp_data(tie=True)
        interaction=self.interaction()
        await self.cog.cup_mvp.callback(self.cog,interaction)
        embed=interaction.followup.send.call_args.kwargs['embed']
        self.assertIn("aucun Pass d'Or attribué automatiquement",embed.footer.text)
        self.assertIn('départage staff requis',embed.footer.text)

    async def add_seeding_matches(self, *, perfect_tie=False, missing_duration=False):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            for phase,opponent_offset in [('round1',12),('round2',6)]:
                for order,team in enumerate(range(1,7),1):
                    duration = 0 if missing_duration and team == 1 and phase == 'round1' else (100 if perfect_tie else 100 + team)
                    stars = 9 if perfect_tie else 9 - (team // 3)
                    destruction = 100 if perfect_tie else 100 - team
                    await db.execute(
                        """
                        INSERT INTO cup_matches(
                            guild_id,draw_id,phase,match_order,team_a,team_b,status,
                            winner_team,loser_team,team_a_stars,team_a_destruction,
                            team_a_duration,team_b_stars,team_b_destruction,
                            team_b_duration,created_at
                        ) VALUES('1',1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            phase,order,team,team+opponent_offset,'completed',team,
                            team+opponent_offset,stars,destruction,duration,8,90,200+team,now,
                        ),
                    )
            await db.commit()

    async def test_seeding_rejects_missing_duration_before_any_write(self):
        await self.add_seeding_matches(missing_duration=True)
        with self.assertRaisesRegex(cup.SeedingIntegrityError,'durée absente'):
            await self.cog.calculate_seeds(1,1,list(range(1,7)))
        self.assertEqual(await self.cog.seeds_map(1,1),{})

    async def test_seeding_rejects_perfect_tie_before_any_write(self):
        await self.add_seeding_matches(perfect_tie=True)
        with self.assertRaisesRegex(cup.SeedingIntegrityError,'Égalité parfaite'):
            await self.cog.calculate_seeds(1,1,list(range(1,7)))
        self.assertEqual(await self.cog.seeds_map(1,1),{})

    async def test_seeding_is_deterministic_and_replaces_all_six_rows(self):
        await self.add_seeding_matches()
        first=await self.cog.calculate_seeds(1,1,list(range(1,7)))
        first_map=await self.cog.seeds_map(1,1)
        second=await self.cog.calculate_seeds(1,1,list(reversed(range(1,7))))
        self.assertEqual(await self.cog.seeds_map(1,1),first_map)
        self.assertEqual(
            [(row['team'],row['seed']) for row in second],
            [(row['team'],row['seed']) for row in first],
        )
        self.assertEqual(len(first_map),6)

    async def test_full_72_player_tournament(self):
        for phase,count,roster in [('round1',12,3),('round2',6,4),('playin',2,5),('semifinal',2,5),('final',1,5)]:
            # Concurrent staff clicks must create exactly one phase.
            await asyncio.gather(*(self.cog.cup_advance.callback(self.cog,self.interaction()) for _ in range(2)))
            matches=await self.cog.get_phase_matches(1,1,phase)
            self.assertEqual(len(matches),count)
            for m in matches:
                for t in [m['team_a'],m['team_b']]:
                    self.assertEqual(len(await self.cog.team_active_members(1,1,t)),roster)
                # Round 2 manual lineup is a separate Discord acceptance check.
                await self.manual(m['id'])
                if phase in ('round1','round2'):
                    candidates=await self.cog.draft_candidates(m['id'])
                    self.assertTrue(await self.cog.execute_draft(self.guild,m['id'],candidates[0]['player_tag'],automatic=True))
            self.assertTrue(await self.cog.phase_completed(1,1,phase))
            self.assertEqual(await self.cog.unresolved_drafts_for_phase(1,1,phase),0)
        state=await self.cog.get_cup_state(1,1)
        self.assertEqual(state['status'],'completed')
        self.assertIsNotNone(state['champion_team'])
        self.assertEqual(len(await self.cog.team_active_members(1,1,state['champion_team'])),5)
        self.cog.announce_champion.assert_awaited_once()

    async def test_one_live_failure_does_not_stop_other_matches_or_drafts(self):
        await self.cog.create_phase_matches(self.guild,1,'round1',[(1,2),(3,4)])
        self.cog.bot.guilds=[self.guild]
        self.cog.process_match_live=AsyncMock(side_effect=[RuntimeError('simulation'),None])
        self.cog.process_pending_drafts=AsyncMock()
        with patch.object(cup.asyncio,'sleep',new=AsyncMock()):
            await self.cog.cup_live_loop.coro(self.cog)
        self.assertEqual(self.cog.process_match_live.await_count,2)
        self.cog.process_pending_drafts.assert_awaited_once_with(self.guild)

    async def test_command_tree_serializes(self):
        import discord
        from discord.ext import commands
        bot=commands.Bot(command_prefix='!',intents=discord.Intents.none())
        tree=self.cog.app_command.to_dict(bot.tree)
        self.assertEqual(tree['name'],'cup')
        self.assertEqual(len(tree['options']),25)
        await bot.close()

    async def test_phase_failure_preserves_all_pairs_and_restart_resumes(self):
        pairs=[(i,i+1) for i in range(1,25,2)]
        self.cog.create_match_channel.side_effect=[
            (NS(id=801),NS(id=901),NS(id=1001)),
            (NS(id=802),NS(id=902),NS(id=1002)),
            RuntimeError('panne simulée'),
        ]
        with self.assertRaises(RuntimeError):
            await self.cog.create_phase_matches(self.guild,1,'round1',pairs)
        before=await self.cog.get_phase_matches(1,1,'round1')
        self.assertEqual([(m['team_a'],m['team_b']) for m in before],pairs)
        self.assertTrue(await self.cog.phase_is_preparing(before[0]))
        restarted=cup.KaiCup(NS(guilds=[]))
        await restarted.setup_database()
        restarted.publish_bracket=AsyncMock()
        restarted.create_match_channel=AsyncMock(return_value=(NS(id=888),NS(id=999),NS(id=1000)))
        self.assertTrue(await restarted.resume_phase_creation(self.guild,1))
        after=await restarted.get_phase_matches(1,1,'round1')
        self.assertEqual([m['id'] for m in before],[m['id'] for m in after])
        self.assertEqual(after[0]['match_channel_id'],'801')
        self.assertEqual(restarted.create_match_channel.await_count,10)
        self.assertFalse(await restarted.phase_is_preparing(after[0]))
        self.assertFalse(await restarted.resume_phase_creation(self.guild,1))

    async def test_preparing_phase_blocks_results_and_api(self):
        self.cog.create_match_channel.side_effect=RuntimeError('panne')
        with self.assertRaises(RuntimeError):
            await self.cog.create_phase_matches(self.guild,1,'round1',[(1,2)])
        m=(await self.cog.get_phase_matches(1,1,'round1'))[0]
        i=await self.manual(m['id'])
        self.assertIn('Phase en préparation',i.followup.send.call_args.args[0])
        self.cog.current_war=AsyncMock()
        await self.cog.process_match_live(self.guild,m)
        self.cog.current_war.assert_not_awaited()
        self.assertIsNone((await self.cog.get_match(m['id']))['winner_team'])

    async def test_saved_pairs_cannot_change_on_resume(self):
        await self.cog.create_phase_matches(self.guild,1,'round1',[(1,2),(3,4)])
        with self.assertRaises(ValueError):
            await self.cog.create_phase_matches(self.guild,1,'round1',[(1,3),(2,4)])
        rows=await self.cog.get_phase_matches(1,1,'round1')
        self.assertEqual([(r['team_a'],r['team_b']) for r in rows],[(1,2),(3,4)])

    async def test_ready_phase_retry_preserves_completed_match(self):
        m=await self.first_match(); await self.manual(m['id'])
        self.cog.create_match_channel.reset_mock()
        await self.cog.create_phase_matches(self.guild,1,'round1',[(1,2)])
        self.assertEqual((await self.cog.get_match(m['id']))['status'],'completed')
        self.cog.create_match_channel.assert_not_awaited()

    async def test_advance_retries_failed_phase_without_extra_phase(self):
        self.cog.create_match_channel.side_effect=RuntimeError('panne')
        i=self.interaction()
        await self.cog.cup_advance.callback(self.cog,i)
        self.assertIn('interrompue',i.followup.send.call_args.args[0])
        self.cog.create_match_channel.side_effect=None
        i=self.interaction()
        await self.cog.cup_advance.callback(self.cog,i)
        self.assertIn('reprise et terminée',i.followup.send.call_args.args[0])
        self.assertEqual(len(await self.cog.get_phase_matches(1,1,'round1')),12)
        self.assertEqual(await self.cog.get_phase_matches(1,1,'round2'),[])

    async def test_roster_integrity_requires_exact_progression_3_4_5(self):
        self.assertEqual(await self.cog.roster_integrity_issues(1,1,[1],3),[])
        issues=await self.cog.roster_integrity_issues(1,1,[1],4)
        self.assertEqual((issues[0]['actual'],issues[0]['expected']),(3,4))
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                "INSERT INTO cup_team_members(guild_id,draw_id,team_number,discord_id,player_tag,player_name,member_order,joined_at) VALUES('1',1,1,'9901','#EXTRA4','Extra 4',4,?)",
                (cup.utc_now(),),
            )
            await db.commit()
        self.assertEqual(await self.cog.roster_integrity_issues(1,1,[1],4),[])
        issues=await self.cog.roster_integrity_issues(1,1,[1],5)
        self.assertEqual(issues[0]['actual'],4)
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                "INSERT INTO cup_team_members(guild_id,draw_id,team_number,discord_id,player_tag,player_name,member_order,joined_at) VALUES('1',1,1,'9902','#EXTRA5','Extra 5',5,?)",
                (cup.utc_now(),),
            )
            await db.commit()
        self.assertEqual(await self.cog.roster_integrity_issues(1,1,[1],5),[])

    async def test_advance_lists_invalid_rosters_before_creating_matches(self):
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                "DELETE FROM cup_team_members WHERE draw_id=1 AND team_number IN (2,4) AND member_order=3"
            )
            await db.commit()
        interaction=self.interaction()
        await self.cog.cup_advance.callback(self.cog,interaction)
        message=interaction.followup.send.call_args.args[0]
        self.assertIn('intégrité des rosters',message)
        self.assertIn('Team 2 : 2/3',message)
        self.assertIn('Team 4 : 2/3',message)
        async with aiosqlite.connect(cup.DATABASE) as db:
            matches=(await (await db.execute("SELECT COUNT(*) FROM cup_matches")).fetchone())[0]
            plans=(await (await db.execute("SELECT COUNT(*) FROM cup_phase_plans")).fetchone())[0]
        self.assertEqual((matches,plans),(0,0))

    async def test_all_six_qualified_rosters_are_checked_before_seeding(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            for team_number in range(1,7):
                max_order=4 if team_number == 1 else 5
                for order in range(4,max_order+1):
                    await db.execute(
                        "INSERT INTO cup_team_members(guild_id,draw_id,team_number,discord_id,player_tag,player_name,member_order,joined_at) VALUES('1',1,?,?,?,?,?,?)",
                        (
                            team_number,
                            str(8000+team_number*10+order),
                            f'#Q{team_number}{order}',
                            f'Qualifié {team_number}-{order}',
                            order,
                            now,
                        ),
                    )
            for order in range(1,13):
                await db.execute(
                    "INSERT INTO cup_matches(guild_id,draw_id,phase,match_order,team_a,team_b,status,winner_team,loser_team,created_at) VALUES('1',1,'round1',?,?,?,?,?,?,?)",
                    (order,order,order+12,'completed',order,order+12,now),
                )
            for order in range(1,7):
                await db.execute(
                    "INSERT INTO cup_matches(guild_id,draw_id,phase,match_order,team_a,team_b,status,winner_team,loser_team,created_at) VALUES('1',1,'round2',?,?,?,?,?,?,?)",
                    (order,order,order+6,'completed',order,order+6,now),
                )
            await db.commit()
        self.cog.calculate_seeds=AsyncMock()
        interaction=self.interaction()
        await self.cog.cup_advance.callback(self.cog,interaction)
        message=interaction.followup.send.call_args.args[0]
        self.assertIn('Team 1 : 4/5',message)
        self.cog.calculate_seeds.assert_not_awaited()
        self.assertEqual(await self.cog.get_phase_matches(1,1,'playin'),[])

    async def test_registration_commands_frozen_after_draw(self):
        self.cog.get_config=AsyncMock()
        self.cog.get_registration_by_discord=AsyncMock()
        for name in ['cup_open','cup_close','cup_unregister']:
            i=self.interaction()
            await getattr(self.cog,name).callback(self.cog,i)
            self.assertIn('verrouillés',i.followup.send.call_args.args[0])
        i=self.interaction()
        await self.cog.cup_setup.callback(self.cog,i,NS(id=8),'#CLAN')
        self.assertIn('verrouillés',i.followup.send.call_args.args[0])
        self.cog.get_config.assert_not_awaited()
        self.cog.get_registration_by_discord.assert_not_awaited()

    async def test_registration_frozen_and_interrupted_draw_is_resumed_not_rerolled(self):
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute("UPDATE cup_draws SET status='error' WHERE id=1")
            await db.commit()
        self.cog.fetch_player=AsyncMock()
        i=self.interaction()
        await self.cog.register_player(i,'#NEW',False)
        self.assertIn('verrouillés',i.followup.send.call_args.args[0])
        self.cog.fetch_player.assert_not_awaited()
        i=self.interaction()
        await self.cog.cup_draw.callback(self.cog,i,test_mode=True)
        self.assertIn('reprise',i.followup.send.call_args.args[0].lower())
        self.assertEqual(len(await self.cog.active_team_numbers(1,1)),24)

    async def test_reset_draw_does_not_freeze_registrations(self):
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute("UPDATE cup_draws SET status='reset' WHERE id=1")
            await db.commit()
        self.assertFalse(await self.cog.registrations_frozen(self.interaction()))

    async def test_interrupted_draw_resumes_without_changing_teams(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute("DELETE FROM cup_team_members WHERE draw_id=1 AND team_number>3")
            await db.execute("DELETE FROM cup_teams WHERE draw_id=1 AND team_number>3")
            await db.execute("UPDATE cup_draws SET status='preparing',team_count=3 WHERE id=1")
            await db.execute(
                "INSERT INTO cup_draw_plans(draw_id,guild_id,announcement_channel_id,status,created_at,updated_at) VALUES(1,'1','799','preparing',?,?)",
                (now,now),
            )
            await db.commit()
        before=[]
        for team_number in range(1,4):
            before.append([m['discord_id'] for m in await self.cog.get_team_members(1,1,team_number)])

        category=NS(id=600)
        channels=[]
        for team_number in range(1,4):
            channel=Mock(spec=discord.TextChannel)
            channel.id=700+team_number
            channel.send=AsyncMock(side_effect=[NS(id=1000+team_number),NS(id=1100+team_number)])
            channels.append(channel)
        self.cog.ensure_teams_category=AsyncMock(return_value=category)
        public_channel=Mock(spec=discord.TextChannel)
        public_channel.id=799
        public_channel.send=AsyncMock(side_effect=[NS(id=1301),NS(id=1302),NS(id=1303)])
        self.guild.get_channel=lambda channel_id: public_channel if channel_id == 799 else None
        self.cog.create_team_discord_space=AsyncMock(side_effect=[
            (NS(id=501),channels[0]),
            (NS(id=502),channels[1]),
            RuntimeError('panne équipe 3'),
        ])
        with self.assertRaises(RuntimeError):
            await self.cog.resume_draw_creation(self.guild,1)
        self.assertEqual((await self.cog.registration_draw(1))['status'],'error')
        self.assertEqual((await self.cog.get_draw_team_step(1,1,1))['status'],'ready')
        self.assertEqual((await self.cog.get_draw_team_step(1,1,2))['status'],'ready')
        self.assertEqual((await self.cog.get_draw_team_step(1,1,3))['status'],'pending')

        restarted=cup.KaiCup(NS(guilds=[]))
        await restarted.setup_database()
        restarted.ensure_teams_category=AsyncMock(return_value=category)
        restarted.create_team_discord_space=AsyncMock(return_value=(NS(id=503),channels[2]))
        self.assertTrue(await restarted.resume_draw_creation(self.guild,1))
        self.assertEqual(restarted.create_team_discord_space.await_count,1)
        self.assertEqual(public_channel.send.await_count,3)
        after=[]
        for team_number in range(1,4):
            after.append([m['discord_id'] for m in await restarted.get_team_members(1,1,team_number)])
        self.assertEqual(after,before)
        self.assertEqual((await restarted.get_active_draw(1))['status'],'active')
        self.assertEqual((await restarted.get_draw_plan(1))['status'],'ready')

    async def test_draw_resume_reuses_saved_roster_message(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute("DELETE FROM cup_team_members WHERE draw_id=1 AND team_number>1")
            await db.execute("DELETE FROM cup_teams WHERE draw_id=1 AND team_number>1")
            await db.execute("UPDATE cup_draws SET status='preparing',team_count=1 WHERE id=1")
            await db.execute(
                "INSERT INTO cup_draw_plans(draw_id,guild_id,status,created_at,updated_at) VALUES(1,'1','preparing',?,?)",
                (now,now),
            )
            await db.commit()
        category=NS(id=600)
        channel=Mock(spec=discord.TextChannel)
        channel.id=701
        channel.fetch_message=AsyncMock(return_value=NS(id=1201))
        channel.send=AsyncMock(side_effect=[NS(id=1201),RuntimeError('welcome indisponible'),NS(id=1202)])
        self.cog.ensure_teams_category=AsyncMock(return_value=category)
        self.cog.create_team_discord_space=AsyncMock(return_value=(NS(id=501),channel))
        with self.assertRaises(RuntimeError):
            await self.cog.resume_draw_creation(self.guild,1)
        step=await self.cog.get_draw_team_step(1,1,1)
        self.assertEqual((step['roster_message_id'],step['welcome_message_id']),('1201',None))

        restarted=cup.KaiCup(NS(guilds=[]))
        await restarted.setup_database()
        restarted.ensure_teams_category=AsyncMock(return_value=category)
        restarted.create_team_discord_space=AsyncMock(return_value=(NS(id=501),channel))
        self.assertTrue(await restarted.resume_draw_creation(self.guild,1))
        self.assertEqual(channel.send.await_count,3)
        channel.fetch_message.assert_awaited_once_with(1201)
        step=await restarted.get_draw_team_step(1,1,1)
        self.assertEqual((step['welcome_message_id'],step['status']),('1202','ready'))

    async def test_staff_diagnostics_prioritizes_operational_blockers(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                "INSERT INTO cup_config(guild_id,cup_name,kai_clan_tag,registration_open,internal_limit,external_limit) VALUES('1','KAI CUP','#KAI',0,60,12)"
            )
            await db.execute(
                "INSERT INTO cup_draw_plans(draw_id,guild_id,status,created_at,updated_at) VALUES(1,'1','ready',?,?)",
                (now,now),
            )
            for team_number in range(1,25):
                await db.execute(
                    "INSERT INTO cup_draw_team_steps(guild_id,draw_id,team_number,status,updated_at) VALUES('1',1,?,'ready',?)",
                    (team_number,now),
                )
            await db.execute("DELETE FROM cup_team_clans WHERE team_number IN (22,23,24)")
            member_ids=[row[0] for row in await (await db.execute(
                "SELECT discord_id FROM cup_team_members WHERE draw_id=1 ORDER BY id LIMIT 60"
            )).fetchall()]
            for discord_id in member_ids:
                await db.execute(
                    "INSERT INTO cup_checkins(guild_id,draw_id,discord_id,checked_at) VALUES('1',1,?,?)",
                    (discord_id,now),
                )
            cur=await db.execute(
                """
                INSERT INTO cup_matches(
                    guild_id,draw_id,phase,match_order,team_a,team_b,status,created_at
                ) VALUES('1',1,'round2',1,1,2,'waiting_lineup',?)
                """,
                (now,),
            )
            match_id=int(cur.lastrowid)
            for team_number,count in ((1,4),(2,3)):
                for slot in range(1,count+1):
                    await db.execute(
                        """
                        INSERT INTO cup_lineups(
                            guild_id,draw_id,match_id,team_number,discord_id,
                            player_tag,player_name,slot,is_substitute,created_at
                        ) VALUES('1',1,?,?,?,?,?,?,?,?)
                        """,
                        (
                            match_id,team_number,str(team_number*100+slot),
                            f'#L{team_number}{slot}',f'Lineup {team_number}-{slot}',
                            slot,int(slot==4),now,
                        ),
                    )
            await db.execute(
                "INSERT INTO cup_phase_plans(guild_id,draw_id,phase,pairs_json,status,created_at) VALUES('1',1,'round2','[[1,2]]','preparing',?)",
                (now,),
            )
            await db.execute(
                "INSERT INTO cup_match_finalizations(match_id,guild_id,draw_id,status,last_error,created_at,updated_at) VALUES(900,'1',1,'pending','discord indisponible',?,?)",
                (now,now),
            )
            await db.execute(
                "INSERT INTO cup_drafts(match_id,guild_id,draw_id,winner_team,loser_team,status,deadline,created_at) VALUES(901,'1',1,1,2,'pending','2020-01-01T00:00:00+00:00',?)",
                (now,),
            )
            await db.commit()

        summary=await self.cog.operational_diagnostics(1)
        self.assertEqual((summary['teams_ready'],summary['teams_total']),(24,24))
        self.assertEqual(summary['missing_clan_teams'],[22,23,24])
        self.assertEqual((summary['checkins'],summary['participants_total']),(60,72))
        self.assertEqual(summary['phase_plans_pending'],1)
        self.assertEqual(summary['missing_lineups'],[(match_id,2)])
        self.assertEqual(summary['finalizations_with_error'],[900])
        self.assertEqual(summary['drafts_expired'],[901])
        self.assertIn('/cup advance',self.cog.diagnostics_action(summary))

        self.cog.api_get=AsyncMock(return_value={'name':'Kai','tag':'#KAI'})
        interaction=self.interaction()
        await self.cog.cup_diagnostics.callback(self.cog,interaction)
        embed=interaction.followup.send.call_args.kwargs['embed']
        fields={field.name:field.value for field in embed.fields}
        self.assertIn('24/24',fields['Tirage et équipes'])
        self.assertIn('60/72',fields['Check-in'])
        self.assertIn(f'M{match_id}/T2',fields['Matchs et compositions'])
        self.assertIn('/cup advance',fields['Action staff prioritaire'])

    async def test_discord_infrastructure_diagnostics_lists_missing_resources(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute(
                """
                INSERT INTO cup_event_config(
                    guild_id,event_category_id,matches_category_id,staff_category_id,
                    live_channel_id,results_channel_id,bracket_channel_id,
                    control_channel_id,logs_channel_id,updated_at
                ) VALUES('1','101','102','103','104','105','106','107','108',?)
                """,
                (now,),
            )
            for team_number in range(1,25):
                await db.execute(
                    "UPDATE cup_teams SET role_id=? WHERE draw_id=1 AND team_number=?",
                    (str(900+team_number),team_number),
                )
            cur=await db.execute(
                """
                INSERT INTO cup_matches(
                    guild_id,draw_id,phase,match_order,team_a,team_b,status,
                    match_channel_id,created_at
                ) VALUES('1',1,'round1',1,1,2,'live','999',?)
                """,
                (now,),
            )
            match_id=int(cur.lastrowid)
            await db.commit()

        channels={
            101:Mock(spec=discord.CategoryChannel),
            102:Mock(spec=discord.CategoryChannel),
            103:Mock(spec=discord.CategoryChannel),
            104:Mock(spec=discord.TextChannel),
            105:Mock(spec=discord.TextChannel),
            # 106 cup-bracket est volontairement absent.
            107:Mock(spec=discord.TextChannel),
            108:Mock(spec=discord.TextChannel),
            **self.team_channels,
        }
        roles={
            900+team:Mock(spec=discord.Role)
            for team in range(1,25)
            if team != 2
        }
        guild=NS(
            id=1,me=None,
            get_channel=lambda channel_id:channels.get(channel_id),
            get_role=lambda role_id:roles.get(role_id),
        )
        draw=await self.cog.registration_draw(1)
        summary=await self.cog.discord_infrastructure_diagnostics(guild,draw)
        self.assertEqual((summary['event_ok'],summary['event_expected']),(7,8))
        self.assertEqual(summary['event_missing'],['cup-bracket'])
        self.assertEqual(summary['team_missing_roles'],[2])
        self.assertEqual(summary['team_missing_channels'],[])
        self.assertEqual(summary['match_missing_channels'],[match_id])

    async def test_diagnostics_action_prioritizes_event_repair(self):
        summary={
            'draw':{'status':'active'},'phase_plans_pending':0,
            'finalizations_with_error':[],'drafts_expired':[],
            'missing_clan_teams':[],'missing_lineups':[],
            'participants_total':72,'checkins':72,
            'finalizations_pending':[],'drafts_pending':[],
        }
        infrastructure={
            'event_missing':['cup-live'],'event_inaccessible':[],
            'team_missing_roles':[],'team_missing_channels':[],
            'match_missing_channels':[],
        }
        self.assertIn(
            '/cup event action:setup',
            self.cog.diagnostics_action(summary,infrastructure),
        )

    async def test_schema_upgrade_preserves_existing_data(self):
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute('DROP TABLE cup_phase_plans')
            await db.commit()
        await self.cog.setup_database()
        await self.cog.setup_database()
        self.assertEqual(len(await self.cog.active_team_numbers(1,1)),24)
        async with aiosqlite.connect(cup.DATABASE) as db:
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM cup_team_members')).fetchone())[0],72)
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM cup_phase_plans')).fetchone())[0],0)
            tables={row[0] for row in await (await db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
            self.assertIn('cup_draw_plans',tables)
            self.assertIn('cup_draw_team_steps',tables)

    async def test_channel_send_failure_reuses_recorded_channel(self):
        import discord
        m=await self.first_match()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute('UPDATE cup_matches SET match_channel_id=NULL,match_message_id=NULL,live_message_id=NULL WHERE id=?',(m['id'],))
            await db.commit()
        category=Mock(spec=discord.CategoryChannel)
        channel=Mock(spec=discord.TextChannel); channel.id=81; channel.topic='test'
        channel.send=AsyncMock(side_effect=[RuntimeError('send failed'),NS(id=91)])
        live=Mock(spec=discord.TextChannel); live.id=82; live.send=AsyncMock(return_value=NS(id=92))
        channels={80:category,82:live}
        async def create(*args,**kwargs):
            channels[81]=channel
            return channel
        self.guild.get_channel=lambda i:channels.get(i)
        self.guild.create_text_channel=AsyncMock(side_effect=create)
        self.guild.text_channels=[]; self.guild.default_role=object(); self.guild.me=None
        self.cog.ensure_event_space=AsyncMock(return_value={'matches_category_id':'80','live_channel_id':'82'})
        with self.assertRaises(RuntimeError):
            await cup.KaiCup.create_match_channel(self.cog,self.guild,m['id'],1,2,'round1',1)
        saved=await self.cog.get_match(m['id'])
        self.assertEqual(saved['match_channel_id'],'81')
        self.assertIsNone(saved['match_message_id'])
        await cup.KaiCup.create_match_channel(self.cog,self.guild,m['id'],1,2,'round1',1)
        self.guild.create_text_channel.assert_awaited_once()
        saved=await self.cog.get_match(m['id'])
        self.assertEqual((saved['match_message_id'],saved['live_message_id']),('91','92'))

    async def test_live_send_failure_reuses_match_message(self):
        import discord
        m=await self.first_match()
        async with aiosqlite.connect(cup.DATABASE) as db:
            await db.execute('UPDATE cup_matches SET match_channel_id=NULL,match_message_id=NULL,live_message_id=NULL WHERE id=?',(m['id'],))
            await db.commit()
        category=Mock(spec=discord.CategoryChannel)
        message=NS(id=91)
        channel=Mock(spec=discord.TextChannel); channel.id=81; channel.topic='test'
        channel.send=AsyncMock(return_value=message)
        channel.fetch_message=AsyncMock(return_value=message)
        live=Mock(spec=discord.TextChannel); live.id=82
        live.send=AsyncMock(side_effect=[RuntimeError('live failed'),NS(id=92)])
        channels={80:category,82:live}
        async def create(*args,**kwargs):
            channels[81]=channel
            return channel
        self.guild.get_channel=lambda i:channels.get(i)
        self.guild.create_text_channel=AsyncMock(side_effect=create)
        self.guild.text_channels=[]; self.guild.default_role=object(); self.guild.me=None
        self.cog.ensure_event_space=AsyncMock(return_value={'matches_category_id':'80','live_channel_id':'82'})
        with self.assertRaises(RuntimeError):
            await cup.KaiCup.create_match_channel(self.cog,self.guild,m['id'],1,2,'round1',1)
        await cup.KaiCup.create_match_channel(self.cog,self.guild,m['id'],1,2,'round1',1)
        self.guild.create_text_channel.assert_awaited_once()
        channel.send.assert_awaited_once()
        self.assertEqual((await self.cog.get_match(m['id']))['live_message_id'],'92')

    async def test_finalization_resumes_after_progression_failure(self):
        m=await self.first_match()
        original_open_draft=self.cog.open_draft
        self.cog.open_draft=AsyncMock(side_effect=RuntimeError('draft indisponible'))
        interaction=await self.manual(m['id'])
        self.assertIn('Résultat enregistré et verrouillé',interaction.followup.send.call_args.args[0])
        job=await self.cog.get_match_finalization(m['id'])
        self.assertEqual((job['status'],job['result_announced'],job['progression_done']),('pending',1,0))
        self.assertIn('draft indisponible',job['last_error'])
        refresh=await self.manual(m['id'],action='refresh')
        self.assertIn('Reprise encore incomplète',refresh.followup.send.call_args.args[0])
        self.cog.open_draft=original_open_draft
        self.assertTrue(await self.cog.resume_match_finalization(self.guild,m['id']))
        job=await self.cog.get_match_finalization(m['id'])
        self.assertEqual(job['status'],'done')
        self.cog.announce_match_result.assert_awaited_once()
        async with aiosqlite.connect(cup.DATABASE) as db:
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM cup_drafts WHERE match_id=?',(m['id'],))).fetchone())[0],1)

    async def test_draft_notification_failure_is_retried(self):
        m=await self.first_match()
        winner_channel=self.team_channels[701]
        winner_channel.send.side_effect=[RuntimeError('discord indisponible'),NS(id=1777)]
        interaction=await self.manual(m['id'])
        self.assertIn('Résultat enregistré et verrouillé',interaction.followup.send.call_args.args[0])
        async with aiosqlite.connect(cup.DATABASE) as db:
            row=await (await db.execute('SELECT status,message_id FROM cup_drafts WHERE match_id=?',(m['id'],))).fetchone()
        self.assertEqual(row,('pending',None))
        self.assertTrue(await self.cog.resume_match_finalization(self.guild,m['id']))
        async with aiosqlite.connect(cup.DATABASE) as db:
            row=await (await db.execute('SELECT status,message_id FROM cup_drafts WHERE match_id=?',(m['id'],))).fetchone()
        self.assertEqual(row,('pending','1777'))
        self.assertEqual((await self.cog.get_match_finalization(m['id']))['status'],'done')

    async def test_final_champion_announcement_is_resumed_without_repeating_progression(self):
        async with aiosqlite.connect(cup.DATABASE) as db:
            for team_number in (1,2):
                for order in (4,5):
                    await db.execute(
                        "INSERT INTO cup_team_members(guild_id,draw_id,team_number,discord_id,player_tag,player_name,member_order,joined_at) VALUES('1',1,?,?,?,?,?,?)",
                        (
                            team_number,
                            str(9000+team_number*10+order),
                            f'#F{team_number}{order}',
                            f'Finaliste {team_number}-{order}',
                            order,
                            cup.utc_now(),
                        ),
                    )
            await db.commit()
        ids=await self.cog.create_phase_matches(self.guild,1,'final',[(1,2)])
        match_id=ids[0]
        self.cog.eliminate_team=AsyncMock()
        self.cog.announce_champion=AsyncMock(side_effect=[RuntimeError('annonce champion'),None])
        interaction=await self.manual(match_id,stars_a=15,stars_b=14)
        self.assertIn('Résultat enregistré et verrouillé',interaction.followup.send.call_args.args[0])
        job=await self.cog.get_match_finalization(match_id)
        self.assertEqual((job['progression_done'],job['champion_done'],job['status']),(1,0,'pending'))
        state=await self.cog.get_cup_state(1,1)
        self.assertEqual(state['champion_team'],1)
        self.assertTrue(await self.cog.resume_match_finalization(self.guild,match_id))
        self.cog.eliminate_team.assert_awaited_once()
        self.assertEqual(self.cog.announce_champion.await_count,2)
        self.assertEqual(self.cog.announce_match_result.await_count,1)
        self.assertEqual((await self.cog.get_match_finalization(match_id))['status'],'done')

    async def test_pending_finalizations_continue_after_one_failure(self):
        now=cup.utc_now()
        async with aiosqlite.connect(cup.DATABASE) as db:
            for match_id in (101,102):
                await db.execute(
                    "INSERT INTO cup_match_finalizations(match_id,guild_id,draw_id,status,created_at,updated_at) VALUES(?,'1',1,'pending',?,?)",
                    (match_id,now,now),
                )
            await db.commit()
        self.cog.resume_match_finalization=AsyncMock(side_effect=[RuntimeError('premier'),True])
        await self.cog.process_pending_finalizations(self.guild,1)
        self.assertEqual(self.cog.resume_match_finalization.await_count,2)
        self.cog.cup_log.assert_awaited_once()

if __name__=='__main__':
    unittest.main(verbosity=2)
