import asyncio
import io
import json
import logging
import os
import subprocess
import time
import sys
from pathlib import Path
from typing import Optional, Set

# Ensure script directory is in sys.path and set as cwd
_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))
os.chdir(_SCRIPT_DIR)

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, GEMINI_API_KEY
from db import (
    get_projects,
    get_conversations_for_project,
    get_thread_binding,
    set_thread_binding,
    find_thread_for_conversation,
    update_thread_synced_line,
    update_thread_model,
    update_thread_policy,
    load_mappings,
    delete_thread_binding,
    get_conversation_usage,
)
from transcriber import transcribe_audio_bytes
from antigravity_client import (
    start_new_conversation,
    send_message_to_conversation,
    count_transcript_lines,
    monitor_response,
    get_user_uploaded_dir,
    get_transcript_path,
    get_cascade_models,
    get_active_antigravity_model,
    set_active_antigravity_model,
    BRAIN_DIR,
)
import desktop_mcp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("discord.voice_client").setLevel(logging.ERROR)
logger = logging.getLogger("AntigravityBridge")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-flight prompts and active turns to prevent duplicate echo
_ACTIVE_DISCORD_PROMPTS: Set[str] = set()
_CURRENTLY_HANDLING_TURNS: Set[str] = set()

# --- Interactive Agent Question UI ---

class CustomAnswerModal(discord.ui.Modal, title="Write Custom Response"):
    answer_text = discord.ui.TextInput(
        label="Your Answer",
        style=discord.TextStyle.paragraph,
        placeholder="Type your answer to Antigravity...",
        required=True
    )

    def __init__(self, conv_id: str, project_id: Optional[str], parent_msg: Optional[discord.Message] = None):
        super().__init__()
        self.conv_id = conv_id
        self.project_id = project_id
        self.parent_msg = parent_msg

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        ans = self.answer_text.value.strip()
        if self.parent_msg:
            try:
                await self.parent_msg.edit(content=f"✅ **You responded:**\n> {ans}", view=None)
            except Exception:
                pass
        await interaction.followup.send(f"Submitted answer: `{ans[:80]}`", ephemeral=True)
        send_message_to_conversation(self.conv_id, f"I choose / answer: {ans}", project_id=self.project_id)


class AskQuestionView(discord.ui.View):
    def __init__(self, channel: discord.Thread | discord.TextChannel, conv_id: str, project_id: Optional[str], question: str, options: list):
        super().__init__(timeout=3600)
        self.channel = channel
        self.conv_id = conv_id
        self.project_id = project_id
        self.question = question
        self.options = options

        # Buttons if 1 to 4 options, Select Menu if > 4
        if len(options) <= 4 and options:
            for idx, opt in enumerate(options):
                btn = discord.ui.Button(
                    label=opt[:80],
                    style=discord.ButtonStyle.primary if idx == 0 else discord.ButtonStyle.secondary
                )
                btn.callback = self.make_button_callback(opt)
                self.add_item(btn)
        elif options:
            select_opts = [
                discord.SelectOption(label=opt[:90], value=opt[:90])
                for opt in options[:25]
            ]
            sel = discord.ui.Select(placeholder="Select an option...", options=select_opts)
            sel.callback = self.select_callback
            self.add_item(sel)

        # Custom write-in button
        custom_btn = discord.ui.Button(label="✏️ Write Custom Answer", style=discord.ButtonStyle.success)
        custom_btn.callback = self.custom_callback
        self.add_item(custom_btn)

    def make_button_callback(self, opt_text: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(content=f"✅ **Selected:** *{opt_text}*", view=self)
            send_message_to_conversation(self.conv_id, f"I choose: {opt_text}", project_id=self.project_id)
        return callback

    async def select_callback(self, interaction: discord.Interaction):
        selected_opt = interaction.data["values"][0]
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(content=f"✅ **Selected:** *{selected_opt}*", view=self)
        send_message_to_conversation(self.conv_id, f"I choose: {selected_opt}", project_id=self.project_id)

    async def custom_callback(self, interaction: discord.Interaction):
        modal = CustomAnswerModal(self.conv_id, self.project_id, interaction.message)
        await interaction.response.send_modal(modal)


# --- Interactive Permission / Approval UI ---

class PermissionApprovalView(discord.ui.View):
    def __init__(self, conv_id: str, project_id: Optional[str], action_desc: str):
        super().__init__(timeout=600)
        self.conv_id = conv_id
        self.project_id = project_id
        self.action_desc = action_desc

    @discord.ui.button(label="✅ Allow & Proceed", style=discord.ButtonStyle.success)
    async def allow_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(content=f"✅ **Approved:** {self.action_desc}", view=self)
        send_message_to_conversation(self.conv_id, "Proceed with the operation.", project_id=self.project_id)

    @discord.ui.button(label="❌ Deny & Abort", style=discord.ButtonStyle.danger)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(content=f"❌ **Denied:** {self.action_desc}", view=self)
        send_message_to_conversation(self.conv_id, "Denied. Do not run this operation.", project_id=self.project_id)


# --- Model Selection View ---

class ModelSelect(discord.ui.Select):
    def __init__(self, thread_id: int, models: list, current_model_id: str):
        self.thread_id = thread_id
        options = []
        for m in models[:25]:
            label = m["label"]
            mid = m["model_id"]
            qp = m["quota_percent"]
            desc = f"Quota: {qp}% remaining"
            emoji = m.get("emoji", "🤖")
            is_default = (mid == current_model_id)
            options.append(discord.SelectOption(
                label=label[:100],
                value=mid,
                description=desc[:100],
                emoji=emoji,
                default=is_default
            ))
        super().__init__(placeholder="Select an Antigravity Model & Reasoning Tier...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_model_id = self.values[0]
        selected_opt = next((o for o in self.options if o.value == selected_model_id), None)
        selected_label = selected_opt.label if selected_opt else selected_model_id

        # Update thread mapping & Antigravity app state
        update_thread_model(self.thread_id, selected_label, selected_model_id)
        set_active_antigravity_model(selected_model_id)

        embed = discord.Embed(
            title="🤖 Model Switched Successfully",
            description=f"Active model set to **{selected_label}**\n(`{selected_model_id}`)",
            color=0x4285F4
        )
        embed.set_footer(text="Subsequent prompts and turns in this session will use this model.")
        await interaction.response.send_message(embed=embed, ephemeral=False)


class ModelSelectView(discord.ui.View):
    def __init__(self, thread_id: int, models: list, current_model_id: str):
        super().__init__(timeout=180)
        self.add_item(ModelSelect(thread_id, models, current_model_id))


# --- Policy Selection View ---

class PolicySelectView(discord.ui.View):
    def __init__(self, thread_id: int):
        super().__init__(timeout=120)
        self.thread_id = thread_id

    @discord.ui.button(label="⚡ Auto-Proceed (Default)", style=discord.ButtonStyle.primary)
    async def auto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_thread_policy(self.thread_id, "auto")
        await interaction.response.send_message("🛡️ Policy set to **Auto-Proceed** (Agent executes tools automatically).", ephemeral=False)

    @discord.ui.button(label="🛡️ Require Review", style=discord.ButtonStyle.danger)
    async def review_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        update_thread_policy(self.thread_id, "review")
        await interaction.response.send_message("🛡️ Policy set to **Require Review** (Sensitive actions ask for approval).", ephemeral=False)


# --- Conversation & Project UI ---

class ConversationSelect(discord.ui.Select):
    def __init__(self, project: dict, conversations: list):
        self.project = project
        self.conversations = conversations
        options = []
        for c in conversations[:25]:
            title = (c["title"] or "Untitled")[:90]
            options.append(discord.SelectOption(
                label=title,
                value=c["conversation_id"],
                description=f"ID: {c['conversation_id'][:8]}..."
            ))
        super().__init__(placeholder="Select an existing conversation to resume...", options=options)

    async def callback(self, interaction: discord.Interaction):
        conv_id = self.values[0]
        selected_conv = next((c for c in self.conversations if c["conversation_id"] == conv_id), None)
        title = selected_conv["title"] if selected_conv else "Resumed Session"

        await interaction.response.defer(ephemeral=True)

        existing_thread_id = find_thread_for_conversation(conv_id)
        if existing_thread_id:
            try:
                existing_thread = interaction.guild.get_thread(int(existing_thread_id))
                if not existing_thread:
                    existing_thread = await interaction.guild.fetch_channel(int(existing_thread_id))
                if existing_thread:
                    await interaction.followup.send(
                        f"📌 **Thread already exists:** {existing_thread.mention}\nSwitch to that thread to continue working!",
                        ephemeral=True
                    )
                    return
            except Exception:
                pass

        channel = interaction.channel
        thread = await channel.create_thread(
            name=f"📂 {self.project['name']}: {title[:40]}",
            type=discord.ChannelType.public_thread
        )

        curr_lines = count_transcript_lines(conv_id)
        active_mid = get_active_antigravity_model()
        all_models = get_cascade_models()
        active_m = next((m for m in all_models if m["model_id"] == active_mid), all_models[0] if all_models else None)
        active_label = active_m["label"] if active_m else "Gemini 3.8 Flash (High)"

        set_thread_binding(
            thread_id=thread.id,
            conversation_id=conv_id,
            project_name=self.project["name"],
            workspace_path=self.project["path"],
            title=title,
            project_id=selected_conv.get("project_id") if selected_conv else self.project.get("project_id"),
            last_synced_line=curr_lines,
            model=active_label,
            model_id=active_mid,
            policy="auto"
        )

        embed = discord.Embed(
            title="Connected to Antigravity Conversation",
            description=(
                f"**Project:** `{self.project['name']}`\n"
                f"**Title:** {title}\n"
                f"**ID:** `{conv_id}`\n"
                f"**Active Model:** `{active_label}`\n\n"
                f"🎙️ Send voice notes, screenshots, or text here to control this session!\n"
                f"🔄 Messages typed on PC will also synchronize here in real time."
            ),
            color=0x4285F4
        )
        await thread.send(embed=embed)
        await interaction.followup.send(f"Connected to thread: {thread.mention}", ephemeral=True)


class ProjectModelSelect(discord.ui.Select):
    def __init__(self, parent_view, models: list, current_model_id: str):
        self.parent_view = parent_view
        options = []
        for m in models[:25]:
            label = m["label"]
            mid = m["model_id"]
            qp = m["quota_percent"]
            desc = f"Quota: {qp}% remaining"
            emoji = m.get("emoji", "🤖")
            is_default = (mid == current_model_id)
            options.append(discord.SelectOption(
                label=label[:100],
                value=mid,
                description=desc[:100],
                emoji=emoji,
                default=is_default
            ))
        super().__init__(placeholder="Select Model for New Task (Optional)...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected_model_id = self.values[0]
        selected_m = next((m for m in self.parent_view.models if m["model_id"] == selected_model_id), None)
        if selected_m:
            self.parent_view.selected_model = selected_m
            for opt in self.options:
                opt.default = (opt.value == selected_model_id)

        lbl = self.parent_view.selected_model["label"]
        qp = self.parent_view.selected_model["quota_percent"]
        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Workspace:** `{self.parent_view.project['path']}`\n"
            f"**Conversations:** {self.parent_view.project['conv_count']}\n\n"
            f"🤖 **Model for New Tasks:** `{lbl}` ({qp}% quota remaining)\n"
            f"*(Tap 'Start New Task' below to launch)*"
        )
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class ProjectActionsView(discord.ui.View):
    def __init__(self, project: dict, models: list, default_model: Optional[dict] = None):
        super().__init__(timeout=180)
        self.project = project
        self.models = models
        self.selected_model = default_model or (models[0] if models else {
            "label": "Gemini 3.8 Flash (High)",
            "model_id": "MODEL_PLACEHOLDER_M318",
            "quota_percent": 65.0
        })

        # Dropdown on row 0
        self.add_item(ProjectModelSelect(self, models, self.selected_model["model_id"]))

    @discord.ui.button(label="➕ Start New Task", style=discord.ButtonStyle.primary, row=1)
    async def new_task_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = NewTaskModal(self.project, self.selected_model)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📂 Resume Existing Thread", style=discord.ButtonStyle.secondary, row=1)
    async def resume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        convs = get_conversations_for_project(self.project["name"])
        if not convs:
            await interaction.response.send_message(f"No existing conversations found for `{self.project['name']}`.", ephemeral=True)
            return

        view = discord.ui.View()
        view.add_item(ConversationSelect(self.project, convs))
        await interaction.response.send_message(f"Select a conversation to resume in **{self.project['name']}**:", view=view, ephemeral=True)


class NewTaskModal(discord.ui.Modal, title="Start New Antigravity Task"):
    task_title = discord.ui.TextInput(
        label="Task Title",
        placeholder="e.g. Fix login form validation",
        required=True,
        max_length=60
    )
    initial_prompt = discord.ui.TextInput(
        label="Initial Prompt (or describe goal)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Inspect the auth controller and write a test case...",
        required=True
    )

    def __init__(self, project: dict, selected_model: dict):
        super().__init__()
        self.project = project
        self.selected_model = selected_model

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        selected_label = self.selected_model["label"]
        selected_model_id = self.selected_model["model_id"]

        thread = await channel.create_thread(
            name=f"🚀 {self.project['name']}: {self.task_title.value}",
            type=discord.ChannelType.public_thread
        )

        status_msg = await thread.send(f"⏳ Initializing session for `{self.task_title.value}` in `{self.project['name']}` (Model: **{selected_label}**)...")

        try:
            cid = start_new_conversation(
                workspace_path=self.project["path"],
                title=self.task_title.value,
                initial_prompt=self.initial_prompt.value,
                project_id=self.project.get("project_id"),
                model_id=selected_model_id
            )

            existing_tid = find_thread_for_conversation(cid)
            if existing_tid and str(existing_tid) != str(thread.id):
                logger.warning(f"Conversation {cid} was already bound to thread {existing_tid}. Preventing duplicate bind.")

            curr_lines = count_transcript_lines(cid)
            set_thread_binding(
                thread_id=thread.id,
                conversation_id=cid,
                project_name=self.project["name"],
                workspace_path=self.project["path"],
                title=self.task_title.value,
                project_id=self.project.get("project_id"),
                last_synced_line=curr_lines,
                model=selected_label,
                model_id=selected_model_id,
                policy="auto"
            )

            await status_msg.edit(content=f"✅ Session `{cid[:8]}...` started with **{selected_label}** in `{self.project['name']}`. Processing prompt...")
            await interaction.followup.send(f"Thread created: {thread.mention}", ephemeral=True)

            binding = get_thread_binding(thread.id)
            await handle_agent_turn(thread, cid, status_msg, start_lines=0, binding=binding)
        except Exception as e:
            logger.error(f"Failed to start new conversation: {e}")
            await status_msg.edit(content=f"❌ Failed to start conversation: {e}")


class ProjectSelect(discord.ui.Select):
    def __init__(self, projects: list):
        options = []
        for p in projects[:25]:
            desc = f"{p['conv_count']} convs | {p['path'][:40]}"
            options.append(discord.SelectOption(
                label=p["name"][:90],
                value=p["name"],
                description=desc[:100]
            ))
        super().__init__(placeholder="Choose an Antigravity Project...", options=options)
        self.projects_map = {p["name"]: p for p in projects}

    async def callback(self, interaction: discord.Interaction):
        project_name = self.values[0]
        project = self.projects_map[project_name]

        models = get_cascade_models()
        active_mid = get_active_antigravity_model()
        active_model = next((m for m in models if m["model_id"] == active_mid), models[0] if models else None)
        active_label = active_model["label"] if active_model else "Gemini 3.8 Flash (High)"
        active_quota = f"{active_model['quota_percent']}%" if active_model else "N/A"

        embed = discord.Embed(
            title=f"Project: {project['name']}",
            description=(
                f"**Workspace:** `{project['path']}`\n"
                f"**Conversations:** {project['conv_count']}\n\n"
                f"🤖 **Model for New Tasks:** `{active_label}` ({active_quota} quota remaining)\n"
                f"*(Change model using the dropdown below before starting a task)*"
            ),
            color=0x34A853
        )
        view = ProjectActionsView(project, models=models, default_model=active_model)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ProjectView(discord.ui.View):
    def __init__(self, projects: list):
        super().__init__(timeout=180)
        self.add_item(ProjectSelect(projects))


# --- Undo / Rollback Implementation ---

async def perform_undo(channel: discord.Thread | discord.TextChannel, target_message: discord.Message, binding: dict, requester: discord.Member | discord.User):
    conversation_id = binding["conversation_id"]
    workspace_path = binding.get("workspace_path", "")
    project_id = binding.get("project_id")

    progress_msg = await channel.send("⏪ *Rolling back changes and cleaning thread history...*")

    deleted_count = 0
    try:
        async for msg in channel.history(after=target_message, limit=100, oldest_first=True):
            if msg.id != target_message.id and msg.id != progress_msg.id:
                try:
                    await msg.delete()
                    deleted_count += 1
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error purging messages: {e}")

    git_msg = "No Git repository detected (edits handled by Antigravity rollback)."
    if workspace_path and Path(workspace_path).exists():
        try:
            check_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace_path, capture_output=True, text=True)
            if check_git.returncode == 0:
                status_res = subprocess.run(["git", "status", "--porcelain"], cwd=workspace_path, capture_output=True, text=True)
                if status_res.stdout.strip():
                    stash_name = f"antigravity_undo_{int(time.time())}"
                    subprocess.run(["git", "stash", "push", "-u", "-m", stash_name], cwd=workspace_path, capture_output=True, text=True)
                    git_msg = f"Working tree reverted to HEAD (uncommitted changes preserved in `{stash_name}`)."
                else:
                    git_msg = "Git working tree was already clean."
        except Exception as e:
            git_msg = f"Git revert error: {e}"

    try:
        undo_prompt = (
            f"⚠️ [SYSTEM: USER INITIATED ROLLBACK]\n"
            f"The user has rolled back the conversation to the message:\n"
            f"> \"{target_message.clean_content[:200]}\"\n"
            f"Please discard and revert any subsequent instructions or code changes from this session. "
            f"Confirm the rollback and await the user's next prompt."
        )
        send_message_to_conversation(conversation_id, undo_prompt, project_id=project_id)
    except Exception as e:
        logger.error(f"Error notifying Antigravity of rollback: {e}")

    embed = discord.Embed(
        title="⏪ Rollback Complete",
        description=f"Thread rolled back to message by **{target_message.author.display_name}**.",
        color=0xE67E22
    )
    embed.add_field(name="Messages Purged", value=f"`{deleted_count}` messages deleted", inline=True)
    embed.add_field(name="Workspace Status", value=git_msg, inline=False)
    embed.add_field(name="Target Message", value=f"> {target_message.clean_content[:150]}" if target_message.clean_content else "*(Attachment)*", inline=False)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    await channel.send(embed=embed)


# --- Commands ---

@bot.tree.command(name="projects", description="List Antigravity projects and start or resume a conversation")
async def projects_cmd(interaction: discord.Interaction):
    prjs = get_projects()
    if not prjs:
        await interaction.response.send_message("No Antigravity projects found on this machine.", ephemeral=True)
        return
    view = ProjectView(prjs)
    await interaction.response.send_message("📁 **Select an Antigravity Project:**", view=view, ephemeral=True)


@bot.tree.command(name="model", description="List and switch between all 18 available Antigravity models and reasoning tiers")
async def model_cmd(interaction: discord.Interaction):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This command only works inside an active Antigravity thread.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    models = get_cascade_models()
    active_mid = get_active_antigravity_model()
    curr_label = binding.get("model") or "Gemini 3.8 Flash (High)"

    # Compute live quota summary
    gemini_quota = next((f"{m['quota_percent']}%" for m in models if "Gemini" in m["label"]), "N/A")
    claude_quota = next((f"{m['quota_percent']}%" for m in models if "Claude" in m["label"]), "100%")
    gpt_quota = next((f"{m['quota_percent']}%" for m in models if "GPT" in m["label"]), "100%")

    embed = discord.Embed(
        title="🤖 Antigravity Model Selection",
        description=(
            f"**Current Session Model:** `{curr_label}`\n\n"
            f"**📊 Live Quota Status:**\n"
            f"• ⚡ **Gemini Family (Flash & Pro):** `{gemini_quota} remaining`\n"
            f"• 🎭 **Claude Family (Opus & Sonnet 5.5):** `{claude_quota} remaining`\n"
            f"• 🌐 **OpenAI GPT-OSS 120B:** `{gpt_quota} remaining`\n\n"
            f"*Select any model and reasoning tier from the menu below:*"
        ),
        color=0x4285F4
    )
    view = ModelSelectView(interaction.channel_id, models, active_mid)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="policy", description="Configure tool approval policy (Auto-Proceed or Require Review)")
async def policy_cmd(interaction: discord.Interaction):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This command only works inside an active Antigravity thread.", ephemeral=True)
        return

    curr_policy = binding.get("policy", "auto").capitalize()
    view = PolicySelectView(interaction.channel_id)
    await interaction.response.send_message(f"🛡️ **Current Permission Policy:** `{curr_policy}`\nChoose an execution policy:", view=view, ephemeral=True)


@bot.tree.command(name="usage", description="Display token usage, step count, and tool breakdown for this session")
async def usage_cmd(interaction: discord.Interaction):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This command only works inside an active Antigravity thread.", ephemeral=True)
        return

    conv_id = binding["conversation_id"]
    stats = get_conversation_usage(conv_id)
    model_name = binding.get("model") or "Gemini 3.8 Flash (High)"
    policy_name = binding.get("policy", "auto").capitalize()

    models = get_cascade_models()
    m_info = next((m for m in models if m["label"] == model_name or m["model_id"] == binding.get("model_id")), None)
    quota_str = f"({m_info['quota_percent']}% quota)" if m_info else ""

    embed = discord.Embed(
        title="📊 Session Usage & Telemetry",
        color=0x4285F4
    )
    embed.add_field(name="Project", value=f"`{binding['project_name']}`", inline=True)
    embed.add_field(name="Active Model", value=f"`{model_name}` {quota_str}", inline=True)
    embed.add_field(name="Policy Mode", value=f"`{policy_name}`", inline=True)

    embed.add_field(name="Total Steps", value=str(stats['step_count']), inline=True)
    embed.add_field(name="User Prompts", value=str(stats['user_prompts']), inline=True)
    embed.add_field(name="Agent Turns", value=str(stats['agent_turns']), inline=True)

    tokens_str = f"~{stats['estimated_tokens']:,}" if stats['estimated_tokens'] else "N/A"
    embed.add_field(name="Transcript Footprint", value=f"{stats['transcript_size_kb']} KB ({tokens_str} tokens)", inline=False)

    if stats['tool_breakdown']:
        tools_list = []
        for tname, tcount in sorted(stats['tool_breakdown'].items(), key=lambda x: x[1], reverse=True):
            tools_list.append(f"• `{tname}`: {tcount} calls")
        embed.add_field(name="🛠️ Tool Invocations", value="\n".join(tools_list[:8]), inline=False)
    else:
        embed.add_field(name="🛠️ Tool Invocations", value="*No tools recorded.*", inline=False)

    embed.add_field(name="Session Status", value=f"`{stats['status']}` (Last active: `{stats['last_modified']}`)", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="undo", description="Undo changes up to a replied message and delete subsequent chats")
async def undo_slash(interaction: discord.Interaction):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This command only works inside an active Antigravity thread.", ephemeral=True)
        return

    await interaction.response.send_message(
        "💡 To undo to a specific point:\n"
        "1. **Long-press / right-click** the message you want to roll back to.\n"
        "2. Tap **Apps** $\\rightarrow$ **⏪ Undo to here**\n"
        "*(Or reply to the target message with `!undo`)*",
        ephemeral=True
    )


@bot.tree.context_menu(name="⏪ Undo to here")
async def undo_context_menu(interaction: discord.Interaction, message: discord.Message):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This command only works inside an active Antigravity thread.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await perform_undo(interaction.channel, message, binding, interaction.user)
    await interaction.followup.send("Rollback executed.", ephemeral=True)


@bot.command(name="undo")
async def undo_prefix(ctx: commands.Context):
    binding = get_thread_binding(ctx.channel.id)
    if not binding:
        await ctx.reply("This command only works inside an active Antigravity thread.")
        return

    if not ctx.message.reference or not ctx.message.reference.message_id:
        await ctx.reply("⚠️ Please reply to the message you want to roll back to with `!undo`.")
        return

    try:
        target_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        await perform_undo(ctx.channel, target_message, binding, ctx.author)
    except Exception as e:
        await ctx.reply(f"❌ Failed to execute rollback: {e}")


@bot.tree.command(name="status", description="Check current thread binding and conversation status")
async def status_cmd(interaction: discord.Interaction):
    binding = get_thread_binding(interaction.channel_id)
    if not binding:
        await interaction.response.send_message("This channel/thread is not linked to an Antigravity conversation.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Active Antigravity Session",
        color=0x4285F4
    )
    embed.add_field(name="Project", value=f"`{binding['project_name']}`", inline=True)
    embed.add_field(name="Title", value=binding['title'], inline=True)
    embed.add_field(name="Conversation ID", value=f"`{binding['conversation_id']}`", inline=False)
    model_label = binding.get("model") or "Gemini 3.8 Flash (High)"
    embed.add_field(name="Model", value=f"`{model_label}`", inline=True)
    embed.add_field(name="Policy", value=f"`{binding.get('policy', 'auto').capitalize()}`", inline=True)
    await interaction.response.send_message(embed=embed)


# --- Jarvis Desktop & PC Remote Commands ---

@bot.tree.command(name="screen", description="Take an instant screenshot of your PC desktop and view it on Discord")
async def screen_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        shot = await asyncio.to_thread(desktop_mcp.take_screenshot)
        fpath = shot.get("file_path")
        if not fpath or not os.path.exists(fpath):
            await interaction.followup.send("❌ Failed to capture screenshot.")
            return

        embed = discord.Embed(
            title="🖥️ PC Desktop Snapshot",
            description=f"**Active Window:** `{shot.get('active_window', 'Unknown')}`\n**Resolution:** `{shot.get('width')}x{shot.get('height')}`",
            color=0x00FF7F
        )
        embed.set_footer(text="Jarvis Desktop Assistant")
        file = discord.File(fpath, filename="desktop_snapshot.png")
        embed.set_image(url="attachment://desktop_snapshot.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception as e:
        await interaction.followup.send(f"❌ Error capturing screen: {e}")


@bot.tree.command(name="pc_status", description="Inspect PC vitals (CPU, RAM, Disks, Uptime, Active Window, Audio)")
async def pc_status_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        vitals = await asyncio.to_thread(desktop_mcp.get_system_vitals)
        audio = await asyncio.to_thread(desktop_mcp.get_audio_volume)

        embed = discord.Embed(
            title="⚡ Jarvis PC Status & Vitals",
            color=0x1E90FF
        )
        embed.add_field(name="CPU Load", value=f"`{vitals.get('cpu_usage_percent')}%`", inline=True)
        embed.add_field(name="RAM Usage", value=f"`{vitals.get('ram_used_gb')} / {vitals.get('ram_total_gb')} GB ({vitals.get('ram_usage_percent')}%)`", inline=True)
        embed.add_field(name="PC Uptime", value=f"`{vitals.get('uptime_hours')} hours`", inline=True)

        mute_badge = "🔇 Muted" if audio.get("is_muted") else "🔊 Unmuted"
        embed.add_field(name="Master Audio", value=f"`{audio.get('master_volume')}%` ({mute_badge})", inline=True)
        embed.add_field(name="Active Window", value=f"`{vitals.get('active_window', 'None')[:45]}`", inline=True)

        disk_lines = []
        for dev, dinfo in vitals.get("disks", {}).items():
            disk_lines.append(f"**{dev}**: `{dinfo['free_gb']} GB free` ({dinfo['percent_used']}% used)")
        if disk_lines:
            embed.add_field(name="Disks", value="\n".join(disk_lines), inline=False)

        embed.set_footer(text="Jarvis Desktop Assistant")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error reading PC status: {e}")


@bot.tree.command(name="launch", description="Launch an app, open a file/folder, or open a URL on your PC")
@app_commands.describe(target="Application (calc, notepad, spotify, code), file path, directory, or URL")
async def launch_cmd(interaction: discord.Interaction, target: str):
    await interaction.response.defer()
    try:
        res = await asyncio.to_thread(desktop_mcp.open_app_or_file, target)
        if res.get("status") == "ok":
            await interaction.followup.send(f"🚀 **Jarvis launched:** `{target}`")
        else:
            await interaction.followup.send(f"❌ Failed to launch `{target}`: {res.get('message')}")
    except Exception as e:
        await interaction.followup.send(f"❌ Launch error: {e}")


@bot.tree.command(name="volume", description="Set master audio volume (0-100) or toggle mute")
@app_commands.describe(level="Volume level (0-100)", mute="Set True to mute, False to unmute")
async def volume_cmd(interaction: discord.Interaction, level: Optional[int] = None, mute: Optional[bool] = None):
    await interaction.response.defer()
    try:
        if level is not None:
            await asyncio.to_thread(desktop_mcp.set_audio_volume, level)
        if mute is not None:
            await asyncio.to_thread(desktop_mcp.mute_audio, mute)
        
        info = await asyncio.to_thread(desktop_mcp.get_audio_volume)
        status_str = f"Volume set to **{info['master_volume']}%**"
        if info['is_muted']:
            status_str += " (🔇 Muted)"
        else:
            status_str += " (🔊 Active)"
        await interaction.followup.send(f"🔊 {status_str}")
    except Exception as e:
        await interaction.followup.send(f"❌ Audio error: {e}")


@bot.tree.command(name="focus", description="Bring a window to the foreground on your PC")
@app_commands.describe(window="Window title substring or process ID")
async def focus_cmd(interaction: discord.Interaction, window: str):
    await interaction.response.defer()
    try:
        res = await asyncio.to_thread(desktop_mcp.focus_window, window)
        if res.get("status") == "ok":
            await interaction.followup.send(f"🪟 Focused: `{res.get('title')}`")
        else:
            await interaction.followup.send(f"❌ {res.get('message')}")
    except Exception as e:
        await interaction.followup.send(f"❌ Focus error: {e}")


@bot.tree.command(name="hotkey", description="Send a keyboard shortcut to your PC (e.g. win+d, alt+tab, ctrl+s)")
@app_commands.describe(keys="Keys joined by plus or space, e.g. 'win+d', 'alt+tab', 'ctrl+shift+esc'")
async def hotkey_cmd(interaction: discord.Interaction, keys: str):
    await interaction.response.defer()
    try:
        parts = [k.strip() for k in keys.replace("+", " ").split() if k.strip()]
        res = await asyncio.to_thread(desktop_mcp.hotkey, parts)
        await interaction.followup.send(f"⌨️ Sent shortcut: `{' + '.join(parts)}`")
    except Exception as e:
        await interaction.followup.send(f"❌ Hotkey error: {e}")


# --- 2-Way Background Synchronization Task ---

@tasks.loop(seconds=2.0)
async def background_sync_task():
    mappings = load_mappings()
    for thread_id_str, binding in list(mappings.items()):
        conv_id = binding.get("conversation_id")
        if not conv_id:
            continue

        # Skip if currently handled by an active live monitor turn
        if conv_id in _CURRENTLY_HANDLING_TURNS:
            continue

        tpath = get_transcript_path(conv_id)
        if not tpath.exists():
            continue

        last_synced = binding.get("last_synced_line", 0)
        try:
            with open(tpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue

        total_lines = len(lines)
        if total_lines <= last_synced:
            continue

        thread = bot.get_channel(int(thread_id_str))
        if not thread:
            try:
                thread = await bot.fetch_channel(int(thread_id_str))
            except Exception:
                continue

        new_slice = lines[last_synced:total_lines]

        # Check if a new turn started on the PC
        pc_turn_started = False
        for idx, line_str in enumerate(new_slice):
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                step = json.loads(line_str)
                stype = step.get("type")
                source = step.get("source")
                content = step.get("content", "")

                # 1. User typed directly on PC
                if source == "USER_EXPLICIT" and stype == "USER_INPUT":
                    is_from_discord = any(p in content for p in _ACTIVE_DISCORD_PROMPTS)
                    if is_from_discord:
                        continue

                    clean_content = content
                    if "<USER_REQUEST>" in clean_content:
                        clean_content = clean_content.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0].strip()

                    if "<ADDITIONAL_METADATA>" in clean_content:
                        clean_content = clean_content.split("<ADDITIONAL_METADATA>")[0].strip()

                    if clean_content:
                        await thread.send(f"💻 **[PC User]:**\n>>> {clean_content[:1800]}")

                    # Start live working status for PC task!
                    status_msg = await thread.send("⏳ *Agent is working on PC request...*")
                    _CURRENTLY_HANDLING_TURNS.add(conv_id)
                    start_lines = last_synced + idx + 1
                    asyncio.create_task(handle_agent_turn(thread, conv_id, status_msg, start_lines, binding=binding))
                    pc_turn_started = True
                    break
            except Exception as e:
                logger.error(f"Error parsing synced line: {e}")

        if pc_turn_started:
            continue

        # Fallback: Check if there is an active ongoing agent turn in progress without an explicit USER_INPUT line in new_slice
        has_active_agent_work = False
        for line_str in reversed(new_slice):
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                step = json.loads(line_str)
                if step.get("source") == "MODEL" and step.get("type") == "PLANNER_RESPONSE" and step.get("tool_calls"):
                    has_active_agent_work = True
                    break
                elif step.get("source") == "MODEL" and step.get("type") == "PLANNER_RESPONSE" and step.get("content"):
                    break
            except Exception:
                pass

        if has_active_agent_work:
            status_msg = await thread.send("⏳ *Agent is working on PC request...*")
            _CURRENTLY_HANDLING_TURNS.add(conv_id)
            asyncio.create_task(handle_agent_turn(thread, conv_id, status_msg, last_synced, binding=binding))
            continue

        # Otherwise, sync standard completed lines
        update_thread_synced_line(thread_id_str, total_lines)
        for line_str in new_slice:
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                step = json.loads(line_str)
                stype = step.get("type")
                source = step.get("source")
                content = step.get("content", "")

                # Fallback question handling
                if source == "MODEL" and stype == "PLANNER_RESPONSE":
                    tool_calls = step.get("tool_calls", [])
                    for tc in tool_calls:
                        if tc.get("name") == "ask_question":
                            q_args = tc.get("args", {})
                            for q_obj in q_args.get("questions", []):
                                q_text = q_obj.get("question", "Antigravity has a question:")
                                opts = q_obj.get("options", [])
                                q_view = AskQuestionView(thread, conv_id, binding.get("project_id"), q_text, opts)
                                q_embed = discord.Embed(
                                    title="❓ Question from Antigravity",
                                    description=f"**{q_text}**",
                                    color=0x9B59B6
                                )
                                await thread.send(embed=q_embed, view=q_view)

                    if content and not tool_calls:
                        chunks = [content[i:i + 1900] for i in range(0, len(content), 1900)]
                        for chunk in chunks:
                            await thread.send(chunk)
            except Exception as e:
                logger.error(f"Error parsing synced line: {e}")


# --- Message Event Handling ---

async def handle_agent_turn(channel: discord.Thread | discord.TextChannel, conversation_id: str, status_msg: discord.Message, start_lines: int, binding: Optional[dict] = None):
    _CURRENTLY_HANDLING_TURNS.add(conversation_id)
    last_text = ""
    project_id = binding.get("project_id") if binding else None
    policy = binding.get("policy", "auto") if binding else "auto"

    try:
        async for event in monitor_response(conversation_id, start_lines):
            etype = event.get("type")
            if etype == "status":
                text = event.get("text", "")
                if text and text != last_text:
                    last_text = text
                    try:
                        await status_msg.edit(content=f"⏳ {text}")
                    except Exception:
                        pass

            elif etype == "question":
                questions = event.get("questions", [])
                for q_obj in questions:
                    q_text = q_obj.get("question", "Antigravity has a question:")
                    opts = q_obj.get("options", [])
                    q_view = AskQuestionView(channel, conversation_id, project_id, q_text, opts)
                    q_embed = discord.Embed(
                        title="❓ Question from Antigravity",
                        description=f"**{q_text}**",
                        color=0x9B59B6
                    )
                    await channel.send(embed=q_embed, view=q_view)

            elif etype == "final":
                content = event.get("content", "")
                artifacts = event.get("artifacts", [])

                try:
                    await status_msg.edit(content="✅ **Done**")
                except Exception:
                    pass

                if content:
                    chunks = [content[i:i + 1900] for i in range(0, len(content), 1900)]
                    for chunk in chunks:
                        await channel.send(chunk)

                if artifacts:
                    for art_path in artifacts:
                        p = Path(art_path)
                        if p.exists() and p.stat().st_size <= 20 * 1024 * 1024:
                            await channel.send(f"📄 **Generated Artifact:** `{p.name}`", file=discord.File(str(p)))

                update_thread_synced_line(channel.id, count_transcript_lines(conversation_id))
                return
    finally:
        try:
            update_thread_synced_line(channel.id, count_transcript_lines(conversation_id))
        except Exception:
            pass
        _CURRENTLY_HANDLING_TURNS.discard(conversation_id)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    binding = get_thread_binding(message.channel.id)
    if not binding:
        await bot.process_commands(message)
        return

    if message.content.strip().startswith("!undo"):
        await bot.process_commands(message)
        return

    conversation_id = binding["conversation_id"]
    prompt_text = message.content.strip()
    image_paths = []

    # Handle Attachments
    for attachment in message.attachments:
        ctype = attachment.content_type or ""
        fname = attachment.filename.lower()

        if ctype.startswith("audio/") or fname.endswith((".ogg", ".mp3", ".wav", ".m4a")):
            notify_msg = await message.reply("🎙️ *Transcribing voice memo via Gemini...*")
            try:
                audio_bytes = await attachment.read()
                transcription, model_used = await asyncio.wait_for(
                    asyncio.to_thread(transcribe_audio_bytes, audio_bytes, content_type=ctype),
                    timeout=45.0
                )
                if transcription:
                    prompt_text = f"{prompt_text}\n{transcription}".strip()
                    await notify_msg.edit(content=f"🎙️ **Transcribed ({model_used}):** *\"{transcription}\"*")
                else:
                    await notify_msg.edit(content=f"🎙️ **Transcribed ({model_used}):** *(silent or empty audio)*")
            except asyncio.TimeoutError:
                logger.error("Voice transcription timed out after 45s")
                await notify_msg.edit(content="⚠️ Transcription timed out (Gemini API took longer than 45s).")
            except Exception as e:
                logger.error(f"Transcription error: {e}")
                await notify_msg.edit(content=f"⚠️ Transcription failed: {e}")

        elif ctype.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            upload_dir = get_user_uploaded_dir(conversation_id)
            save_path = upload_dir / f"{attachment.id}_{attachment.filename}"
            await attachment.save(save_path)
            image_paths.append(str(save_path))

    if not prompt_text and not image_paths:
        return

    full_prompt = prompt_text
    if image_paths:
        img_refs = "\n".join([f"[Attached Image]: {p}" for p in image_paths])
        full_prompt = f"{img_refs}\n\n{full_prompt}".strip()

    status_msg = await message.channel.send("⏳ *Sending prompt to Antigravity...*")
    _ACTIVE_DISCORD_PROMPTS.add(full_prompt)

    try:
        start_lines = count_transcript_lines(conversation_id)
        send_message_to_conversation(conversation_id, full_prompt, project_id=binding.get("project_id"))
        await status_msg.edit(content="⏳ *Agent is working on your request...*")
        await handle_agent_turn(message.channel, conversation_id, status_msg, start_lines, binding=binding)
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await status_msg.edit(content=f"❌ Error communicating with Antigravity: {e}")
    finally:
        _ACTIVE_DISCORD_PROMPTS.discard(full_prompt)


@bot.event
async def on_ready():
    logger.info(f"Antigravity Discord Bot logged in as {bot.user} (ID: {bot.user.id})")

    if not background_sync_task.is_running():
        background_sync_task.start()
        logger.info("2-Way Background Synchronization Task started.")

    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced)} slash commands to guild {DISCORD_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands globally")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN is missing. Please populate discord_bridge/.env")
        exit(1)
    bot.run(DISCORD_BOT_TOKEN)
