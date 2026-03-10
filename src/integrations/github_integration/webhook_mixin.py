"""
Webhook-related methods for GitHubIntegration.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger('shadowops')


class WebhookMixin:

    async def start_webhook_server(self):
        """Start the webhook HTTP server"""
        if not self.enabled:
            self.logger.info("ℹ️ GitHub webhooks disabled in config")
            return

        self.app = web.Application()
        self.app.router.add_post('/webhook', self.webhook_handler)
        self.app.router.add_get('/health', self.health_check)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        # Try binding to configured port; on conflict, fall back to 9090
        ports_to_try = [self.webhook_port]
        if self.webhook_port != 9090:
            ports_to_try.append(9090)

        for port in ports_to_try:
            try:
                self.site = web.TCPSite(
                    self.runner, '127.0.0.1', port,
                    reuse_address=True, reuse_port=True
                )
                await self.site.start()
                self.webhook_port = port
                self.logger.info(f"🚀 GitHub webhook server started on port {port}")
                break
            except OSError as e:
                self.logger.error(f"❌ GitHub webhook server konnte Port {port} nicht binden: {e}")
                continue
        else:
            self.logger.error("   GitHub Webhooks werden deaktiviert, bitte Port/Service prüfen.")
            self.enabled = False
            return

    async def mark_bot_ready_and_process_queue(self):
        """
        Mark bot as ready and process any pending webhooks that arrived during startup.
        Should be called by the bot after it's fully initialized.
        """
        self.bot_ready = True

        if not self.pending_webhooks:
            self.logger.info("✅ Bot marked as ready - no pending webhooks")
            return

        pending_count = len(self.pending_webhooks)
        self.logger.info(f"🔄 Bot ready - processing {pending_count} pending webhook(s)...")

        # Process all pending webhooks
        processed = 0
        failed = 0

        for webhook in self.pending_webhooks:
            try:
                event_type = webhook['event_type']
                payload = webhook['payload']
                received_at = webhook['received_at']

                self.logger.info(f"📋 Processing queued {event_type} webhook (received at {received_at})")

                # Route to appropriate handler
                handler = self.event_handlers.get(event_type)
                if handler:
                    await handler(payload)
                    processed += 1
                else:
                    self.logger.debug(f"ℹ️ No handler for queued event type: {event_type}")
                    processed += 1

            except Exception as e:
                self.logger.error(f"❌ Error processing queued webhook: {e}", exc_info=True)
                failed += 1

        # Clear the queue
        self.pending_webhooks.clear()

        self.logger.info(f"✅ Processed {processed} pending webhooks ({failed} failed)")

    async def stop_webhook_server(self):
        """Stop the webhook HTTP server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        await self.stop_local_polling()
        self.logger.info("🛑 GitHub webhook server stopped")

    async def ensure_project_webhooks(self):
        """Ensure GitHub webhooks exist for configured projects."""
        if not self.auto_create_webhooks:
            return

        github_token = self._get_github_token()
        if not github_token:
            self.logger.warning("⚠️ GitHub Token fehlt - Auto-Webhook Setup übersprungen")
            return

        if not self.webhook_public_url:
            self.logger.warning("⚠️ github.webhook_public_url fehlt - Auto-Webhook Setup übersprungen")
            return

        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        if not projects:
            return

        for project_name, project_config in projects.items():
            repo_url = project_config.get('repo_url') or project_config.get('repository_url')
            if not repo_url:
                repo_path = project_config.get('path')
                if repo_path:
                    from pathlib import Path
                    repo_url = self._get_repo_url(Path(repo_path))
            if not repo_url:
                continue

            await self._ensure_webhook_for_repo(
                project_name=project_name,
                repo_url=repo_url,
                github_token=github_token
            )

    async def _ensure_webhook_for_repo(self, project_name: str, repo_url: str, github_token: str) -> None:
        repo_slug = self._parse_github_repo_slug(repo_url)
        if not repo_slug:
            self.logger.warning(f"⚠️ Repo URL nicht GitHub-kompatibel: {repo_url}")
            return

        api_base = self._get_github_api_base(repo_url)
        if not api_base:
            self.logger.warning(f"⚠️ Konnte GitHub API Base nicht bestimmen: {repo_url}")
            return

        hooks_url = f"{api_base}/repos/{repo_slug}/hooks"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {github_token}"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(hooks_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Webhook-Check fehlgeschlagen ({resp.status}) für {repo_slug}: {body}"
                        )
                        return
                    hooks = await resp.json()
            except Exception as e:
                self.logger.warning(f"⚠️ Webhook-Check Fehler für {repo_slug}: {e}")
                return

            for hook in hooks:
                config = hook.get('config', {})
                if config.get('url') == self.webhook_public_url:
                    self.logger.info(f"✅ Webhook existiert bereits für {project_name}")
                    return

            payload = {
                "name": "web",
                "active": True,
                "events": self.webhook_events,
                "config": {
                    "url": self.webhook_public_url,
                    "content_type": "json"
                }
            }

            if self.webhook_secret:
                payload["config"]["secret"] = self.webhook_secret

            try:
                async with session.post(hooks_url, json=payload, timeout=20) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Webhook-Erstellung fehlgeschlagen ({resp.status}) für {repo_slug}: {body}"
                        )
                        return
                    self.logger.info(f"✅ Webhook erstellt für {project_name}")
            except Exception as e:
                self.logger.warning(f"⚠️ Webhook-Erstellung Fehler für {repo_slug}: {e}")

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'service': 'github-webhook',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    async def webhook_handler(self, request: web.Request) -> web.Response:
        """
        Handle incoming GitHub webhook requests

        Verifies HMAC signature and routes to appropriate handler
        """
        try:
            # Read request body
            body = await request.read()

            # Verify signature
            if self.webhook_secret:
                signature = request.headers.get('X-Hub-Signature-256', '')
                if not self._verify_signature(body, signature):
                    self.logger.warning("⚠️ Invalid webhook signature")
                    return web.Response(status=401, text="Invalid signature")

            # Parse payload
            payload = json.loads(body)
            event_type = request.headers.get('X-GitHub-Event', 'unknown')

            self.logger.info(f"📥 Received GitHub event: {event_type}")

            # If bot is not ready yet, queue the webhook for later processing
            if not self.bot_ready:
                self.pending_webhooks.append({
                    'event_type': event_type,
                    'payload': payload,
                    'received_at': datetime.now().isoformat()
                })
                self.logger.info(f"📋 Bot not ready yet - queued {event_type} webhook ({len(self.pending_webhooks)} pending)")
                return web.Response(status=202, text="Accepted - queued for processing")

            # Route to appropriate handler
            handler = self.event_handlers.get(event_type)
            if handler:
                await handler(payload)
            else:
                self.logger.debug(f"ℹ️ No handler for event type: {event_type}")

            return web.Response(status=200, text="OK")

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook: {e}", exc_info=True)
            return web.Response(status=500, text=f"Error: {str(e)}")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """
        Verify GitHub webhook HMAC signature

        Args:
            body: Request body bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature.startswith('sha256='):
            return False

        expected_signature = signature.split('=')[1]

        mac = hmac.new(
            self.webhook_secret.encode('utf-8'),
            msg=body,
            digestmod=hashlib.sha256
        )
        calculated_signature = mac.hexdigest()

        return hmac.compare_digest(calculated_signature, expected_signature)

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Public wrapper for webhook signature verification."""
        return self._verify_signature(body, signature)

    def _get_github_token(self) -> Optional[str]:
        env_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_TOKEN')
        if env_token:
            return env_token
        if hasattr(self.config, 'github_token'):
            try:
                token = self.config.github_token
                if token:
                    return token
            except Exception:
                pass
        if isinstance(self.config, dict):
            return self.config.get('github', {}).get('token')
        return None

    def _parse_github_repo_slug(self, repo_url: str) -> Optional[str]:
        if not repo_url:
            return None
        url = repo_url.strip()
        if url.endswith('.git'):
            url = url[:-4]
        if url.startswith('git@'):
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host, path = remainder.split(':', 1)
                if host.endswith('github.com'):
                    return path.strip('/')
                return None
        if url.startswith('https://') or url.startswith('http://'):
            parts = url.split('/')
            if len(parts) >= 5:
                host = parts[2]
                if host.endswith('github.com'):
                    return f"{parts[3]}/{parts[4]}"
        return None

    def _get_github_api_base(self, repo_url: str) -> Optional[str]:
        if not repo_url:
            return None
        url = repo_url.strip()
        if url.startswith('git@'):
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host = remainder.split(':', 1)[0]
                if host.endswith('github.com'):
                    return "https://api.github.com"
                return f"https://{host}/api/v3"
        if url.startswith('https://') or url.startswith('http://'):
            host = url.split('/')[2]
            if host.endswith('github.com'):
                return "https://api.github.com"
            return f"https://{host}/api/v3"
        return None
