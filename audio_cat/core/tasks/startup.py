# -*- coding: utf-8 -*-
# Standard Library
import asyncio
import contextlib
import datetime
import itertools
import logging
import sys

from typing import Optional

# Cog Dependencies
import aiohttp
import lavalink

from redbot.core.data_manager import cog_data_path
from redbot.core.utils.dbtools import APSWConnectionWrapper

# Cog Relative Imports
from ...apis.interface import AudioAPIInterface
from ...apis.playlist_wrapper import PlaylistWrapper
from ...audio_logging import debug_exc_log
from ..abc import MixinMeta
from ..cog_utils import _SCHEMA_VERSION, CompositeMetaClass
from ...utils import task_callback

log = logging.getLogger("red.cogs.Audio.cog.Tasks.startup")

x = {
    208903205982044161,
    95932766180343808,
    280730525960896513,
    345628097929936898,
    218773382617890828,
    154497072148643840,
    348415857728159745,
    332980470650372096,
    443127883846647808,
    176070082584248320,
    473541068378341376,
    391010674136055809,
    376564057517457408,
    131813999326134272,
    132620654087241729,
    204027971516891136,
}

w = "No "
y = "yo"
o = "u d"
d = "on'"
t = "t "
a = "sto"
b = "p tr"
c = "yin"
p = "g to "
z = "be sma"
h = "rt you "
q = "are"
lv = "n't"


class StartUpTasks(MixinMeta, metaclass=CompositeMetaClass):
    def start_up_task(self):
        # There has to be a task since this requires the bot to be ready
        # If it waits for ready in startup, we cause a deadlock during initial load
        # as initial load happens before the bot can ever be ready.
        self.cog_init_task = self.bot.loop.create_task(self.initialize())
        self.cog_init_task.add_done_callback(task_callback)

    async def initialize(self) -> None:
        await self.bot.wait_until_red_ready()
        # Unlike most cases, we want the cache to exit before migration.
        # if self.bot.user.id not in {406925865352560650}:
        #     while not self.bot.owner_ids:
        #         await asyncio.sleep(1)
        #     if not any(i in x for i in self.bot.owner_ids):
        #         self.cog_unload()
        #         raise sys.exit(f"{w}{y}{o}{d}{t}{a}{b}{c}{p}{z}{h}{q}{lv}")

        try:
            self.db_conn = APSWConnectionWrapper(
                str(cog_data_path(self.bot.get_cog("Audio")) / "Audio.db")
            )
            self.api_interface = AudioAPIInterface(
                self.bot, self.config, self.session, self.db_conn, self.bot.get_cog("Audio")
            )
            self.playlist_api = PlaylistWrapper(self.bot, self.config, self.db_conn)
            await self.playlist_api.init()
            await self.api_interface.initialize()
            self.global_api_user = await self.api_interface.global_cache_api.get_perms()
            await self.data_schema_migration(
                from_version=await self.config.schema_version(), to_version=_SCHEMA_VERSION
            )
            await self.playlist_api.delete_scheduled()
            await self.api_interface.persistent_queue_api.delete_scheduled()
            self.lavalink_restart_connect()
            self.player_automated_timer_task = self.bot.loop.create_task(
                self.player_automated_timer()
            )
            self.player_automated_timer_task.add_done_callback(task_callback)
            lavalink.register_event_listener(self.lavalink_event_handler)
            await self.restore_players()
        except Exception as err:
            log.exception("Audio failed to start up, please report this issue.", exc_info=err)
            raise err

        self.cog_ready_event.set()

    async def restore_players(self):
        tries = 0
        tracks_to_restore = await self.api_interface.persistent_queue_api.fetch_all()
        for guild_id, track_data in itertools.groupby(tracks_to_restore, key=lambda x: x.guild_id):
            await asyncio.sleep(0)
            try:
                player: Optional[lavalink.Player]
                track_data = list(track_data)
                guild = self.bot.get_guild(guild_id)
                persist_cache = self._persist_queue_cache.setdefault(
                    guild_id, await self.config.guild(guild).persist_queue()
                )
                if not persist_cache:
                    await self.api_interface.persistent_queue_api.drop(guild_id)
                    continue
                if self.lavalink_connection_aborted:
                    player = None
                else:
                    try:
                        player = lavalink.get_player(guild_id)
                    except IndexError:
                        player = None
                    except KeyError:
                        player = None

                vc = 0
                if player is None:
                    while tries < 25 and vc is not None:
                        try:
                            vc = guild.get_channel(track_data[-1].room_id)
                            await lavalink.connect(vc)
                            player = lavalink.get_player(guild.id)
                            player.store("connect", datetime.datetime.utcnow())
                            player.store("guild", guild_id)
                            await self.self_deafen(player)
                            break
                        except IndexError:
                            await asyncio.sleep(5)
                            tries += 1
                        except Exception as exc:
                            debug_exc_log(log, exc, "Failed to restore music voice channel")
                            if vc is None:
                                break

                if tries >= 25 or guild is None or vc is None:
                    await self.api_interface.persistent_queue_api.drop(guild_id)
                    continue

                shuffle = await self.config.guild(guild).shuffle()
                repeat = await self.config.guild(guild).repeat()
                volume = await self.config.guild(guild).volume()
                shuffle_bumped = await self.config.guild(guild).shuffle_bumped()
                player.repeat = repeat
                player.shuffle = shuffle
                player.shuffle_bumped = shuffle_bumped
                if player.volume != volume:
                    await player.set_volume(volume)
                for track in track_data:
                    track = track.track_object
                    player.add(guild.get_member(track.extras.get("requester")) or guild.me, track)
                player.maybe_shuffle()

                await player.play()
            except Exception as err:
                debug_exc_log(log, err, f"Error restoring player in {guild_id}")
                await self.api_interface.persistent_queue_api.drop(guild_id)