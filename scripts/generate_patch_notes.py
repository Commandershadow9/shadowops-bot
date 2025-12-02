#!/usr/bin/env python3
"""
Generate patch notes for a commit range using the GitHub integration AI system.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from integrations.github_integration import GitHubIntegration
import subprocess


async def get_commits_in_range(repo_path: str, from_commit: str, to_commit: str = "HEAD"):
    """Get commits in a range using git log"""
    cmd = [
        'git', '-C', repo_path, 'log', '--format=%H|||%an|||%ae|||%s|||%b',
        f'{from_commit}..{to_commit}'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error getting commits: {result.stderr}")
        return []

    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('|||')
        if len(parts) >= 4:
            sha, author_name, author_email, subject = parts[:4]
            body = parts[4] if len(parts) > 4 else ''

            # Combine subject and body for full message
            full_message = subject
            if body.strip():
                full_message += '\n\n' + body.strip()

            commits.append({
                'id': sha,
                'message': full_message,
                'author': {
                    'name': author_name,
                    'email': author_email
                },
                'url': f'https://github.com/Commandershadow9/GuildScout/commit/{sha}'
            })

    return list(reversed(commits))  # Oldest first


async def main():
    if len(sys.argv) < 3:
        print("Usage: python generate_patch_notes.py <repo-name> <from-commit> [to-commit]")
        print("Example: python generate_patch_notes.py GuildScout fcafb91 HEAD")
        sys.exit(1)

    repo_name = sys.argv[1]
    from_commit = sys.argv[2]
    to_commit = sys.argv[3] if len(sys.argv) > 3 else "HEAD"

    # Determine repo path
    if repo_name.lower() == 'guildscout':
        repo_path = '/home/cmdshadow/GuildScout'
    elif repo_name.lower() == 'shadowops' or repo_name.lower() == 'shadowops-bot':
        repo_path = '/home/cmdshadow/shadowops-bot'
    else:
        print(f"Unknown repo: {repo_name}")
        sys.exit(1)

    print(f"📝 Generating patch notes for {repo_name}")
    print(f"   Range: {from_commit}..{to_commit}")
    print()

    # Get commits
    commits = await get_commits_in_range(repo_path, from_commit, to_commit)

    if not commits:
        print("No commits found in range!")
        sys.exit(1)

    print(f"Found {len(commits)} commits:")
    for commit in commits:
        msg = commit['message'].split('\n')[0][:60]
        print(f"  - {commit['id'][:7]}: {msg}")
    print()

    # Create a mock GitHub integration for AI service access
    # We need to load the actual bot config
    print("⚠️  Note: This script needs access to the bot's AI service.")
    print("   Please run this through the bot or manually call the AI service.")
    print()

    # For now, just print what would be sent to AI
    print("="*80)
    print("COMMITS TO ANALYZE:")
    print("="*80)
    for commit in commits:
        author = commit['author']['name']
        message = commit['message'].split('\n')[0]
        print(f"- {message} (by {author})")
    print("="*80)
    print()
    print("To get AI-generated patch notes, these commits need to be sent to the")
    print("AI service. You can manually trigger this by:")
    print("1. Creating a new commit that references these changes")
    print("2. Or using the bot's internal AI service directly")


if __name__ == "__main__":
    asyncio.run(main())
