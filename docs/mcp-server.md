# MCP Server — Codex of Critique

Codex of Critique includes a built-in MCP (Model Context Protocol) server that lets LLMs query your review comment database. The primary use case: an LLM working on your code can search past review comments by keyword, see the code diff, read what reviewers said, and apply that institutional knowledge to the current codebase.

## Prerequisites

- Docker services running: `docker compose up -d`
- Dashboard accessible at `http://localhost:8080`
- At least one sync completed (so there's review data to query)

## Enabling the MCP Server

1. Open the dashboard at `http://localhost:8080`
2. Click the gear icon in the tab bar to open Settings
3. In the **MCP Server** section, check **Enable MCP server endpoint**
4. Optionally configure **Default Reviewers** — these are the reviewers whose comments are returned by default when searching. If left empty, it falls back to the Required Reviewers list above.
5. Click **Save**

The MCP server is now available at `http://localhost:8080/mcp`.

## Connecting Claude Code

Add this to your Claude Code MCP configuration (`.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "codex-of-critique": {
      "type": "sse",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Once configured, the MCP tools are automatically available in any Claude Code session.

## Available Tools

### `search_reviews`

Search past code review comments by keyword. This is the primary tool.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `comment_q` | string | — | Keyword search in review comment bodies |
| `snippet_q` | string | — | Keyword search in code snippets |
| `file_path` | string | — | Filter by file path (partial match) |
| `reviewers` | list of strings | configured default reviewers | Filter by reviewer logins |
| `repositories` | list of strings | — | Filter by "owner/repo" |
| `exclude_repositories` | list of strings | — | Exclude "owner/repo" |
| `pr_author` | string | — | Filter by PR author login |
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Results per page (max 100) |

**What it returns per result:**
- `body` — the review comment text
- `diff_hunk` — the code diff around the change
- `snippets` — code snippets (blob excerpt and diff hunk)
- `path`, `file_extension` — file location
- `comment_author_login` — who wrote the comment
- `pr_number`, `pr_title`, `pr_author` — PR context
- `repo_owner`, `repo_name` — repository

**Example prompts:**
- "Search for past review comments about error handling in the auth module"
- "What did rypskar say about retry logic?"
- "Find review comments on files in app/services/"

### `get_activity`

Get PR activity status — pending reviews, unaddressed changes, merged PRs, etc.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `username` | string | — | Filter by user login |
| `category` | string | — | One of: `pending_reviews`, `changes_not_addressed`, `changes_merged`, `comments` |
| `repositories` | list of strings | — | Filter by "owner/repo" |
| `exclude_repositories` | list of strings | — | Exclude "owner/repo" |
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Results per page (max 100) |

### `get_stats`

Get dashboard statistics — repository counts, sync status. No parameters.

### `get_filters`

List available filter values — repositories, PR authors, and reviewers. Use this to discover valid values before calling `search_reviews` or `get_activity`. No parameters.

## Default Reviewer Behavior

By default, `search_reviews` filters results to your configured default reviewers. This ensures the LLM sees comments from your most trusted reviewers first.

To search all reviewers, pass `reviewers=[]` explicitly.

The default reviewers can be configured in the Settings modal under "MCP Server > Default Reviewers". If no MCP-specific reviewers are set, it falls back to the Required Reviewers list.

## Troubleshooting

**MCP server not responding:**
- Check the dashboard is running: `docker compose ps web`
- Verify MCP is enabled in Settings (gear icon)
- Check logs: `docker compose logs web`

**No results from search:**
- Ensure at least one sync has completed
- Try `get_filters` to see what data is available
- Try searching with `reviewers=[]` to include all reviewers

**Connection refused:**
- Verify port 8080 is mapped: `docker compose ps`
- Check the URL in your config matches `http://localhost:8080/mcp`
