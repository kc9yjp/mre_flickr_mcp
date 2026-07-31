"""Workflow command list for the workbench frontend.

Backed by ``agent.prompts_store`` — each DB-defined prompt outside the
``system`` category becomes a workflow button. Photo-context prompts (a
``{photo_id}`` placeholder) are filled in client-side once a photo is
selected; ``{user_nsid}``/``{username}`` are resolved here before the list
reaches the frontend.
"""

from agent import prompts_store


def commands_for_api(nsid: str, username: str = "") -> list[dict]:
    """Return workflow commands with server-side placeholders resolved."""
    data = prompts_store.all_data(nsid)
    system_category_ids = {c["id"] for c in data["categories"] if c["id"] == "system"}
    return [
        {
            "id": p["code"],
            "prompt_id": p["id"],
            "label": p["name"],
            "context": p["context"],
            "category_id": p["category_id"],
            "prompt": prompts_store.resolve_server_variables(p["text"], nsid, username),
        }
        for p in data["prompts"]
        if p["enabled"] and p["category_id"] not in system_category_ids
    ]
