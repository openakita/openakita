---
name: install-store-skill
description: Install a Skill from the OpenAkita Platform Skill Store
system: true
handler: skill_store
tool-name: install_store_skill
category: Platform
---

# install-store-skill

Install a Skill from the OpenAkita Platform Skill Store to the local system.

## When to Use

- User wants to install a specific Skill from the store
- User says "installthis" after browsing search results
- User provides a Skill ID to install

## Workflow

1. (Optional) Call `search_store_skills` first to find the Skill
2. (Optional) Call `get_store_skill_detail` to preview details, license, ratings
3. Call `install_store_skill` with the `skill_id`
4. The system will:
   - Fetch the Skill's install URL from the platform
   - Clone the Skill from its original GitHub repository
   - Auto-reload skills so the Skill is immediately available

## Tools

### install_store_skill
| Parameter | Required | Description |
|-----------|----------|-------------|
| `skill_id` | Yes | The platform Skill ID (from search results) |

### get_store_skill_detail
| Parameter | Required | Description |
|-----------|----------|-------------|
| `skill_id` | Yes | The platform Skill ID to inspect |

### submit_skill_repo
| Parameter | Required | Description |
|-----------|----------|-------------|
| `repo_url` | Yes | GitHub repository URL to submit as a new Skill |

## Important Notes

- Skills are installed from their **original GitHub repository**, not redistributed by the platform
- Each skill's open-source license applies — check the license before using in production
- After installation, the Skill is auto-loaded and immediately usable
- If a skill with the same name already exists locally, it will be updated

## Fallback

If the platform is unavailable, the user can still:
- Install skills directly from GitHub using `install_skill`
- Browse and install from skills.sh marketplace via Setup Center
- Manage local skills using `list_skills`, `enable_skill`, `disable_skill`
