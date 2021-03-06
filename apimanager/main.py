import asyncio
import json
import logging
import operator
from typing import Mapping, Optional, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
from tabulate import tabulate

from .api import API
from .checks import (
    is_api_admin,
    is_api_contributor,
    is_api_user,
    is_api_mod,
    is_not_api_user,
)
from .menus import LeaderboardSource, SimpleHybridMenu
from .utils import User

log = logging.getLogger("red.drapercogs.APIManager")


class APIManager(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.headers: Mapping = {}
        self.cog_is_ready: asyncio.Event = asyncio.Event()
        self.start_up_task: asyncio.Task = asyncio.create_task(self.init())

    async def init(self):
        await self.bot.wait_until_red_ready()
        id_list = list(getattr(self.bot, "_true_owner_ids", self.bot.owner_ids))
        handshake = "||".join(map(str, id_list))
        self.headers = {
            "Authorization": (await self.bot.get_shared_api_tokens("audiodb")).get(
                "api_key"
            ),
            "X-Token": handshake,
        }
        self.cog_is_ready.set()

    @commands.group(name="audioapi")
    @commands.guild_only()
    async def command_audio_api(self, ctx: commands.Context):
        """Access to the Audio API command."""

    @command_audio_api.command(name="showinfo")
    @commands.guild_only()
    async def command_showinfo(
        self, ctx: commands.Context, *, user: Optional[Union[discord.User, int]] = None
    ):
        """Show user info."""
        if not await is_api_user(ctx):
            return

        if user is not None:
            user_id = user.id if isinstance(user, discord.abc.User) else user
            user = await API.get_user(cog=self, member=discord.Object(id=user_id))
        else:
            user = ctx.audio_api_user

        if user:
            new_data = {
                "Name": f"[{user.name}]",
                "User ID": f"[{user.user_id}]",
                "Entries Submitted": f"[{user.entries_submitted}]",
                "Queries": f"[{user.queries}]",
                "Can Read": f"[{user.can_read}]",
                "Can Post": f"[{user.can_post}]",
                "Can Delete": f"[{user.can_delete}]",
            }
            return await ctx.send(
                box(
                    tabulate(
                        list(new_data.items()),
                        missingval="?",
                        tablefmt="plain",
                    ),
                    lang="ini",
                )
            )

        else:
            return await ctx.send("Failed to get user info")

    @command_audio_api.command(name="mytoken")
    @commands.guild_only()
    async def command_mytoken(self, ctx: commands.Context):
        """Get your user Global API details."""
        if not await is_api_user(ctx):
            return
        api_requester = ctx.audio_api_user
        if not api_requester:
            return await ctx.send(
                f"You aren't registered with the API, run `{ctx.clean_prefix}{self.command_apiregister}` to register."
            )
        if int(api_requester.user_id) != ctx.author.id:
            return await ctx.send("Failed to get user info")
        try:
            new_data = {
                "Name": f"[{api_requester.name}]",
                "User ID": f"[{api_requester.user_id}]",
                "Entries Submitted": f"[{api_requester.entries_submitted}]",
                "Can Read": f"[{api_requester.can_read}]",
                "Can Post": f"[{api_requester.can_post}]",
                "Can Delete": f"[{api_requester.can_delete}]",
            }
            await ctx.tick()
            await ctx.author.send(
                box(
                    tabulate(
                        list(new_data.items()),
                        missingval="?",
                        tablefmt="plain",
                    ),
                    lang="ini",
                )
            )
            if api_requester.token is not None and not api_requester.is_blacklisted:
                await ctx.author.send(
                    f"Use: `[p]set api audiodb api_key {api_requester.token}` to set this key on your bot."
                )
        except discord.HTTPException:
            await ctx.send("I can't DM you.")

    @command_audio_api.command(name="lb")
    @commands.guild_only()
    async def command_apilb(self, ctx: commands.Context):
        """Show the API Leaderboard."""
        if not await is_api_user(ctx):
            return
        users = await API.get_all_users(cog=self)
        if not users:
            return await ctx.send("Nothing found")
        data = [
            (u.entries_submitted, int(u.user_id), u.queries, u.name) for u in users if u.can_read
        ]
        if not data:
            return await ctx.send("Nothing found")
        data.sort(key=operator.itemgetter(0), reverse=True)
        await SimpleHybridMenu(
            source=LeaderboardSource(data),
            delete_message_after=True,
            timeout=60,
            clear_reactions_after=True,
        ).start(ctx)

    @command_audio_api.command(name="ban", cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def command_apiban(
        self, ctx: commands.Context, *, user: Union[discord.User, int]
    ):
        """Ban people from API."""
        if not await is_api_admin(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user):
            return await ctx.send("I can't allow you to do that.")

        if isinstance(user, discord.abc.User):
            user_name = str(user)
        else:
            try:
                user = await self.bot.fetch_user(user)
                user_name = str(user)
            except discord.HTTPException:
                user = discord.Object(id=user)
                user_name = "Deleted User"

        banned_user = await API.ban_user(cog=self, member=user, user_name=user_name)
        if not banned_user:
            return await ctx.send("I couldn't ban the user.")
        else:
            return await ctx.send(
                f"I have banned `{banned_user.name} ({banned_user.user_id})`."
            )

    @command_audio_api.command(name="massban", cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def command_mass_apiban(self, ctx: commands.Context, *users: int):
        """Ban multiple people from API."""
        if not await is_api_admin(ctx):
            return
        await API.mass_ban_user(cog=self, users=list(users), user_name="Mass Banned")
        await ctx.tick()

    @command_audio_api.command(name="revoke")
    @commands.guild_only()
    async def command_apirevoke(
        self, ctx: commands.Context, *, user: Union[discord.User, int]
    ):
        """Revoke people's API access."""
        if not await is_api_mod(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user):
            return await ctx.send("I can't allow you to do that.")
        if isinstance(user, discord.abc.User):
            user_name = str(user)
        else:
            try:
                user = await self.bot.fetch_user(user)
                user_name = str(user)
            except discord.HTTPException:
                user = discord.Object(id=user)
                user_name = "Deleted User"
        revoked_user = await API.ban_user(cog=self, member=user, user_name=user_name)
        if not revoked_user:
            return await ctx.send("I couldn't revoke the user's token.")
        else:
            return await ctx.send(
                f"I have revoked `{revoked_user.name} ({revoked_user.user_id})` token."
            )

    @command_audio_api.command(name="user")
    @commands.guild_only()
    async def command_user(self, ctx: commands.Context, user_id: int):
        """Downgrade a user to reader-only status."""
        if not await is_api_mod(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user_id, strict=True):
            return await ctx.send("I can't allow you to do that.")
        api_user = await API.update_user(
            cog=self, member=discord.Object(id=user_id), contrib=False, user=True
        )
        if not api_user:
            return await ctx.send(f"Couldn't update user `{user_id}` at this time.")
        await ctx.send(f"`{api_user.name} ({api_user.user_id})` is now a contributor.")

    @command_audio_api.command(name="contributor")
    @commands.guild_only()
    async def command_apicontributor(self, ctx: commands.Context, user_id: int):
        """Elevate a user to contributor status."""
        if not await is_api_mod(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user_id, strict=True):
            return await ctx.send("I can't allow you to do that.")
        api_user = await API.update_user(
            cog=self, member=discord.Object(id=user_id), contrib=True
        )
        if not api_user:
            return await ctx.send(f"Couldn't update user `{user_id}` at this time.")
        await ctx.send(f"`{api_user.name} ({api_user.user_id})` is now a contributor.")

    @command_audio_api.command(name="mod")
    @commands.guild_only()
    async def command_apimod(self, ctx: commands.Context, user_id: int):
        """Elevate a user to mod status."""
        if not await is_api_admin(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user_id, strict=True):
            return await ctx.send("I can't allow you to do that.")
        api_user = await API.update_user(
            cog=self, member=discord.Object(id=user_id), contrib=True
        )
        if not api_user:
            return await ctx.send(f"Couldn't update user `{user_id}` at this time.")
        await ctx.send(f"`{api_user.name} ({api_user.user_id})` is now a moderator.")

    @command_audio_api.command(name="unban")
    @commands.guild_only()
    async def command_unban(self, ctx: commands.Context, user_id: int):
        """Unban a previously banned user."""
        if not await is_api_admin(ctx):
            return
        api_requester = ctx.audio_api_user
        if not await self.is_allowed_by_hierarchy(api_requester, user_id, strict=True):
            return await ctx.send("I can't allow you to do that.")
        api_user = await API.unban_user(cog=self, member=discord.Object(id=user_id))
        if not api_user:
            return await ctx.send(f"Couldn't update user `{user_id}` at this time.")
        await ctx.send(f"`{api_user.name} ({api_user.user_id})` has benn unbanned.")

    @command_audio_api.command(name="decode")
    @commands.guild_only()
    async def command_decode(self, ctx: commands.Context, *, track: str):
        """Decodes a Base64 encoded audio track.."""
        if not await is_api_contributor(ctx):
            return
        decoded = await API.decode_track(cog=self, track=track)
        if not decoded:
            return await ctx.send(f"Couldn't decode this track, is it valid?.")
        await ctx.send(box(json.dumps(decoded, sort_keys=True, indent=4), lang="json"))

    @command_audio_api.command(name="register")
    @commands.guild_only()
    async def command_apiregister(self, ctx: commands.Context):
        """Register yourself with the Audio API."""
        if not await is_not_api_user(ctx):
            await ctx.send(
                f"{ctx.author} you already registered with me, if want to see your token please use `{ctx.clean_prefix}{self.command_mytoken}`"
            )
            return
        await API.create_user(cog=self, member=ctx.author)
        api_user = await API.get_user(cog=self, member=ctx.author)
        if not api_user:
            return await ctx.send(
                f"Couldn't register {ctx.author} with the API, please try again later."
            )
        ctx.audio_api_user = api_user
        await ctx.tick()
        await ctx.invoke(self.command_mytoken)

    async def is_allowed_by_hierarchy(
        self,
        mod: User,
        user: Union[discord.abc.User, discord.Object, int],
        strict: bool = False,
    ):
        if isinstance(user, (discord.abc.User, discord.Object)):
            user_id = user.id
        else:
            user_id = user
        user = await API.get_user(cog=self, member=discord.Object(id=user_id))
        if not user:
            return not strict
        if user.is_admin or user.is_superuser:
            return False
        if not strict:
            if (mod.is_admin or mod.is_superuser) and any(
                s
                for s in [
                    user.is_mod,
                    user.is_contributor,
                    user.is_user,
                    user.is_guest,
                    user.is_unregistered,
                    user.is_blacklisted,
                ]
            ):
                return True
            if mod.is_mod and any(
                s
                for s in [
                    user.is_contributor,
                    user.is_user,
                    user.is_guest,
                    user.is_unregistered,
                    user.is_blacklisted,
                ]
            ):
                return True
        else:
            if (mod.is_admin or mod.is_superuser) and any(
                s
                for s in [
                    user.is_contributor,
                    user.is_user,
                    user.is_guest,
                    user.is_unregistered,
                    user.is_blacklisted,
                ]
            ):
                return True
            if mod.is_mod and any(
                s
                for s in [
                    user.is_user,
                    user.is_guest,
                    user.is_unregistered,
                    user.is_blacklisted,
                ]
            ):
                return True
        return False
