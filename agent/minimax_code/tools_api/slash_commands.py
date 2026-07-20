"""Canonical slash-command wording (R190).

Fusion of grok-build's ``xai-grok-tools-api/src/slash_commands.rs`` (217 lines).
Shared wording for the slash commands (``/loop``, ``/imagine``,
``/imagine-video``, ``/goal``) so every front-end expands them identically and
the wording cannot drift between hosts.

Zero external dependencies -- pure string constants + format builders. This is
the crate's most self-contained leaf, landed first (R190) to establish the
``tools_api`` package skeleton ahead of the ``config_validation`` (R191) and
``lib.rs`` barrel-reconciliation (R192) leaves.

Rust -> Python adaptation
-------------------------

Rust leans on backslash line-continuation inside string literals
(``"...\\n\\\n ..."``): the trailing backslash consumes the newline *and* all
leading whitespace on the next physical line, joining multi-line source into one
logical line. Python has no equivalent continuation syntax inside ordinary
strings. To mirror Rust's exact output, each multi-line instruction is built as
a parenthesized run of adjacent string literals with explicit ``\\n`` /
``\\n\\n`` separators -- adjacent literals concatenate at compile time with no
extra whitespace, exactly matching Rust's continuation semantics. A trailing
space is left on each continued literal to reproduce the single space Rust
preserves between the last word of one line and the first word of the next
(the whitespace *between* physical lines is stripped, but the trailing space
*on* the line before the backslash is kept). The ``{args}`` / ``{prompt}`` /
``{objective}`` interpolations become f-strings on the final literal of each
run.
"""

from __future__ import annotations

__all__ = [
    "GOAL_COMMAND_NAME",
    "GOAL_RESERVED_SUBCOMMANDS",
    "IMAGE_GEN_TOOL_NAME",
    "IMAGE_TO_VIDEO_TOOL_NAME",
    "IMAGINE_COMMAND_NAME",
    "IMAGINE_VIDEO_COMMAND_NAME",
    "SCHEDULER_CREATE_TOOL_NAME",
    "UPDATE_GOAL_TOOL_NAME",
    "goal_instruction",
    "goal_usage_message",
    "imagine_instruction",
    "imagine_usage_message",
    "imagine_video_instruction",
    "imagine_video_usage_message",
    "loop_schedule_instruction",
    "loop_usage_message",
]


# Canonical tool name advertised by the scheduler create tool. Gating code
# (shell ``CommandAvailability``, pager ``required_tools``, host command lists)
# keys ``/loop`` availability on this name.
SCHEDULER_CREATE_TOOL_NAME = "scheduler_create"


def loop_usage_message() -> str:
    """Hint shown when ``/loop`` is invoked with no arguments."""
    return "\n".join([
        "Usage: /loop [interval] <prompt>",
        "Example: /loop 30m check deploy status",
        "Example: /loop check deploy status every hour",
        "",
        "Tell me how often it should run (e.g. 30m, 1 hour, every 2 days).",
    ])


def loop_schedule_instruction(args: str) -> str:
    """Build the model instruction ``/loop`` expands into for ``args``.

    The model, not brittle host parsing, turns the request into the
    ``scheduler_create`` interval, accepting every natural phrasing and erroring
    on bad input rather than silently defaulting. See :func:`loop_usage_message`.
    """
    return (
        "# /loop -- schedule a recurring prompt\n\n"
        "Parse the input below into an interval and a prompt, "
        "then schedule it with scheduler_create.\n\n"
        "## Deriving the interval\n"
        "Read how often to run from the user's request — however they phrase it — and convert it\n"
        "to a compact `<number><unit>` string, where unit is one of `s` (seconds), `m` (minutes),\n"
        "`h` (hours), or `d` (days). The interval may appear at the start or end of the request;\n"
        "extract it and use the remaining text as the prompt.\n\n"
        "The minimum interval is 60 seconds; shorter values are raised to 60s, so tell the user if that applies.\n\n"
        "If the request contains no interval at all, ask the user how often it should run before\n"
        "scheduling. Do NOT invent or assume a default interval.\n\n"
        "## Action\n"
        "1. Call scheduler_create with: interval (the compact string you derived), prompt,\n"
        "recurring: true, fire_immediately: true. If the interval is unparseable, the tool\n"
        "returns an error — fix the interval string rather than guessing.\n"
        "2. Confirm: what's scheduled, the cadence, that it auto-expires after 7 days,\n"
        "and that they can cancel with scheduler_delete (include the job ID).\n"
        "3. Do NOT execute the prompt inline. The scheduler will fire it immediately.\n\n"
        "## Input\n"
        f"{args}"
    )


# Canonical name of the image generation tool; gates ``/imagine``.
IMAGE_GEN_TOOL_NAME = "image_gen"

# Advertised name of the /imagine command.
IMAGINE_COMMAND_NAME = "imagine"

# Canonical name of the image-to-video tool; gates ``/imagine-video``.
IMAGE_TO_VIDEO_TOOL_NAME = "image_to_video"

# Advertised name of the /imagine-video command.
IMAGINE_VIDEO_COMMAND_NAME = "imagine-video"


def imagine_usage_message() -> str:
    """Hint shown when ``/imagine`` is invoked with no arguments."""
    return (
        "Usage: /imagine <description>\n"
        "Provide a text description to generate an image."
    )


def imagine_instruction(prompt: str) -> str:
    """Build the model instruction ``/imagine`` expands into for ``prompt``."""
    return (
        "Call the image_gen tool immediately, passing the user's prompt below "
        "verbatim — do not rewrite, embellish, or expand it. "
        "After the tool completes, briefly acknowledge and mention "
        "where the image was saved.\n\n"
        f"Prompt: {prompt}"
    )


def imagine_video_usage_message() -> str:
    """Hint shown when ``/imagine-video`` is invoked with no arguments."""
    return (
        "Usage: /imagine-video <description>\n"
        "Provide a text description to generate a video."
    )


def imagine_video_instruction(prompt: str) -> str:
    """Build the model instruction ``/imagine-video`` expands into for ``prompt``."""
    return f"{_IMAGINE_VIDEO_SKILL}\n\nUser prompt: {prompt}"


#: Video workflow guidance injected by ``/imagine-video``. Built as a run of
#: adjacent literals with explicit ``\n`` separators to mirror Rust's
#: backslash line-continuation (see module docstring).
_IMAGINE_VIDEO_SKILL = (
    "# Imagine Video\n\n"
    "Video starts from an image — there is no text-to-video tool. "
    "Default to `image_to_video`; use `reference_to_video` only when the user "
    "explicitly asks for it or a shot genuinely needs multiple reference images.\n\n"
    "## Default: single clip\n\n"
    "Unless the user asks for a long video, multiple scenes, or a multi-shot sequence, "
    "generate **one** video:\n\n"
    "1. Create a source image with `image_gen` that stages the first frame "
    "(composition, subject, lighting).\n"
    "2. Call `image_to_video` with that image and a short prompt describing the motion "
    "or camera move (1–2 sentences, present tense).\n"
    "3. After the tool completes, mention the saved file path so the user can find it.\n\n"
    "## Longer / multi-shot videos\n\n"
    "When the user requests a longer video, multiple scenes, or a narrative sequence:\n\n"
    "1. **Plan the story as shots** — break the idea into distinct shots, one beat each.\n"
    "2. **Favor frequent, short shots** — prefer more 6s clips over fewer long ones; more cuts keep it dynamic.\n"
    "3. **Create each shot's source image** with `image_gen` (or `image_edit` to combine references), keeping characters and settings consistent across shots.\n"
    "4. **Animate each shot with `image_to_video`** — the source image becomes frame 1.\n"
    "5. **Assemble with FFmpeg** using stream copy (`ffmpeg -f concat ... -c copy` — never re-encode). "
    "Keep every shot at the same resolution and frame rate so the concat works. "
    "After assembly, mention the final output path.\n\n"
    "## Shot guidance\n\n"
    "- **Prompt-craft:** one short, vivid moment in present tense with a clear camera movement, in 1–2 sentences.\n"
    "- **Minimal but interesting:** one clear subject, one simple motion or camera move per shot. Avoid complex multi-action animation; make the shot compelling through composition, lighting, and a strong moment.\n"
    "- **Complex source image?** Intricate frames (busy geometry, fine detail, heavy reflections) warp when animated. Keep the subject fixed and move only the camera (slow push-in, orbit, or parallax), or break into simpler shots. For new shots, generate a simpler, animation-friendly base image rather than animating a busy one.\n"
    "- **`image_to_video` animates from frame 1** — stage the first frame with `image_gen`/`image_edit` before animating.\n"
    "- **Aspect ratio:** set it on the source image (`image_gen` `aspect_ratio`); don't re-crop an existing video.\n"
    "- **Duration:** 6s or 10s only (prefer 6s); round to the nearest.\n"
    "- **Real people:** reference-first — drive the video from a verified reference image; never animate a named person without one.\n"
    "- Don't loop the same clip unless asked."
)


UPDATE_GOAL_TOOL_NAME = "update_goal"

GOAL_COMMAND_NAME = "goal"

#: Bare subcommand tokens reserved for goal lifecycle control rather than
#: being treated as an objective, matching the shell's /goal grammar.
GOAL_RESERVED_SUBCOMMANDS = ("status", "pause", "resume", "clear", "edit")


def goal_usage_message() -> str:
    """Hint shown when ``/goal`` is invoked with no arguments."""
    return (
        "Usage: /goal <objective>\n"
        "Set an objective to work toward until it is complete."
    )


def goal_instruction(objective: str) -> str:
    """Build the model instruction ``/goal`` expands into for ``objective``."""
    return (
        "# /goal -- pursue an objective\n\n"
        f"A goal has been set: {objective}\n\n"
        "Work directly on this goal and carry it as far as you can. Deliver "
        "everything the user asked for yourself: no follow-up questions, no "
        "manual steps left for the user. If the conversation continues, keep "
        "pursuing the goal until it is complete.\n\n"
        "TRACKING: break the objective into concrete steps and track them "
        "(use your todo tool if one is available), marking each done as you "
        "finish it.\n\n"
        "VERIFY AS YOU GO: test each change on the real path before moving on. "
        "A completion claim must be backed by evidence produced in this "
        "session, not assumptions.\n\n"
        'Call update_goal(completed: true, message: "summary") ONLY when the '
        "goal is fully achieved. "
        'Call update_goal(blocked_reason: "reason") '
        "only when truly stuck after 3+ consecutive failed attempts at the "
        "same problem. "
        'Call update_goal(message: "status note") to log '
        "progress along the way. If update_goal returns an error, continue "
        "working the goal and report status in your reply instead.\n\n"
        "Start now."
    )
