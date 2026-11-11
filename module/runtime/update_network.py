"""Runtime update networking; bootstrap deploy modules remain unchanged.

The application downloads Git objects over HTTP/3 first, then lets local Git
inspect or apply them. The original Git transport remains the recovery path
when the configured CDN cannot provide a matching update.
"""

import subprocess

from deploy.git import GitManager
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.git_over_cdn.endpoints import CLOUDFLARE_UPDATE_URLS, FALLBACK_UPDATE_URLS
from module.base import http
from module.logger import logger


class RuntimeGitOverCdnClient(GitOverCdnClient):
    """Reuse the pack format and Git operations with runtime HTTP networking."""

    @staticmethod
    def _create_session(max_retries=3):
        session = http.Session(download=True, retries=max_retries)
        session.trust_env = False
        return session

    def close(self):
        session = self.__dict__.pop('session', None)
        if session is not None:
            session.close()

    def fetch(self):
        """Download missing objects and update the remote ref without resetting HEAD."""
        if not self.current_commit or not self.latest_commit:
            return False
        if self.current_commit != self.latest_commit and not self.download_pack():
            return False
        try:
            # Never publish a remote ref to a manifest whose commit is missing.
            self.git_command('cat-file', '-e', f'{self.latest_commit}^{{commit}}')
        except (OSError, subprocess.CalledProcessError) as error:
            self.logger.warning(f'CDN commit is unavailable: {type(error).__name__}')
            return False
        return self.update_refs()


class RuntimeGitManager(GitManager):
    """Application-only network overrides; deploy never imports this class."""

    @property
    def goc_client(self):
        client = RuntimeGitOverCdnClient(
            url=CLOUDFLARE_UPDATE_URLS,
            fallback_urls=FALLBACK_UPDATE_URLS,
            folder=self.root_filepath,
            source='origin',
            branch=self.Branch,
            git=self.git,
        )
        client.logger = logger
        return client

    def fetch_update(self, source='origin', branch=None, max_retry=3, delay=1):
        """Prepare a version check through CDN, falling back to the existing Git fetch."""
        branch = branch or self.Branch
        if self.GitOverCdn and source == 'origin' and branch == self.Branch:
            client = self.goc_client
            try:
                if client.fetch():
                    return
            finally:
                client.close()
            logger.warning('CDN fetch failed, falling back to Git')
        return self._fetch_with_retry(source, branch, max_retry=max_retry, delay=delay)

    def git_install(self):
        """Apply CDN objects or use the existing repository update fallback."""
        logger.hr('Update AzurPilot', 0)
        if self.GitOverCdn:
            client = self.goc_client
            try:
                if client.update():
                    return
            finally:
                client.close()
            logger.warning('CDN update failed, falling back to Git')
        self.git_repository_init(repo=self.Repository, source='origin', branch=self.Branch,
                                 proxy=self.GitProxy, ssl_verify=self.SSLVerify)
