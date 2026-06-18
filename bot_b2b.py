from __future__ import annotations

import asyncio
import logging
import sys
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from config import settings
from database import init_db
from factory import generate_website
from orchestrator import (
    _gh_create_repo,
    _gh_push_file,
    _slugify,
    _ver_ensure_project,
    _ver_poll_until_ready,
    _ver_trigger_deployment,
)

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    await init_db()
    try:
        synced = await bot.tree.sync()
        logger.info("✅ Logged in as %s | %d slash commands synced.", bot.user, len(synced))
    except discord.DiscordException as exc:
        logger.error("Failed to sync slash commands: %s", exc)


@bot.tree.command(
    name="generate",
    description="🏭 Ghost Factory — generate and deploy a premium site for an agency client.",
)
@app_commands.describe(
    name="Business name (e.g. Johnson Plumbing Co.)",
    niche="Industry or niche (e.g. plumber, dentist, gym, roofer)",
    city="City and country (e.g. Austin TX, London UK) — defaults to New York",
    phone="Client phone number — defaults to placeholder",
)
async def generate_cmd(
    interaction: discord.Interaction,
    name: str,
    niche: str,
    city: str = "New York",
    phone: str = "+1 (555) 000-0000",
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=False)

    # ── Loading embed ────────────────────────────────────────────
    embed_loading = discord.Embed(
        title="⚙️  Factory Running…",
        description=(
            f"**Business:** {name}\n"
            f"**Niche:** {niche}\n"
            f"**City:** {city}\n"
            f"**Phone:** {phone}\n\n"
            "🤖 Gemini is writing the website copy and code.\n"
            "📦 GitHub repo will be created and files pushed.\n"
            "🚀 Vercel will deploy the site automatically.\n\n"
            "*Estimated time: 45–90 seconds.*"
        ),
        color=discord.Color.orange(),
    )
    embed_loading.set_footer(
        text=f"Hybrid King • B2B Ghost Factory • Requested by {interaction.user.display_name}"
    )
    loading_msg = await interaction.followup.send(embed=embed_loading, wait=True)

    start_ts = time.monotonic()
    repo_full_name = "N/A"

    try:
        async with httpx.AsyncClient(http2=False, timeout=60.0) as client:
            # ── Step 1: Generate website via Gemini ──────────────
            website = await generate_website(
                business_name=name,
                niche=niche,
                language="English",
                phone=phone,
                city=city,
            )

            # ── Step 2: Create GitHub repo ────────────────────────
            ts = int(time.time())
            project_name = f"hk-b2b-{_slugify(name)}-{ts}"[:63]
            repo_full_name, repo_id = await _gh_create_repo(client, project_name)

            # ── Step 3: Push files to GitHub ──────────────────────
            await _gh_push_file(
                client, repo_full_name, "index.html", website.index_html, "Add index.html"
            )
            if website.style_css.strip():
                await _gh_push_file(
                    client, repo_full_name, "style.css", website.style_css, "Add style.css"
                )

            # ── Step 4: Link Vercel project to GitHub repo ────────
            await _ver_ensure_project(client, project_name, repo_full_name)
            await asyncio.sleep(4)

            # ── Step 5: Trigger deployment ────────────────────────
            dep_id = await _ver_trigger_deployment(
                client, project_name, repo_full_name, repo_id
            )

            # ── Step 6: Poll until live ───────────────────────────
            live_url = await _ver_poll_until_ready(client, dep_id, timeout_sec=180)

    except Exception as exc:
        logger.error(
            "B2B generation failed for '%s': %s", name, exc, exc_info=True
        )
        embed_err = discord.Embed(
            title="❌  Generation Failed",
            description=(
                f"An error occurred while processing **{name}**:\n"
                f"```{str(exc)[:900]}```\n"
                "Check `bot.log` for the full traceback."
            ),
            color=discord.Color.red(),
        )
        embed_err.set_footer(text="Hybrid King • B2B Ghost Factory")
        await loading_msg.edit(embed=embed_err)
        return

    elapsed = time.monotonic() - start_ts

    # ── Success embed ────────────────────────────────────────────
    embed_ok = discord.Embed(
        title="✅  Site is LIVE — Ready to Invoice",
        description=(
            f"> *Deployed in `{elapsed:.0f}s` from command to live URL.*"
        ),
        color=discord.Color.green(),
        url=live_url,
    )
    embed_ok.add_field(name="🏢  Business", value=name, inline=True)
    embed_ok.add_field(name="🔧  Niche", value=niche, inline=True)
    embed_ok.add_field(name="📍  City", value=city, inline=True)
    embed_ok.add_field(
        name="🔗  Live URL",
        value=f"[{live_url}]({live_url})",
        inline=False,
    )
    embed_ok.add_field(
        name="📁  GitHub Repo",
        value=f"[{repo_full_name}](https://github.com/{repo_full_name})",
        inline=False,
    )
    embed_ok.add_field(
        name="💰  Next Step",
        value="Send the live URL to your client and collect payment.",
        inline=False,
    )
    embed_ok.set_footer(
        text=(
            f"Hybrid King • B2B Ghost Factory • "
            f"Delivered by {interaction.user.display_name}"
        )
    )
    await loading_msg.edit(embed=embed_ok)
    logger.info("B2B site deployed for '%s' → %s (%.0fs)", name, live_url, elapsed)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ],
    )
    async with bot:
        await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())