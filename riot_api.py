"""Minimal Riot API client with rate limiting + retry.

Two routing schemes matter:
  - platform routing (na1, euw1, kr...)   -> League-V4, Summoner-V4
  - regional routing (americas, asia, europe, sea) -> Match-V5
"""
import logging
import time
from collections import deque

import requests

import config

log = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket over two windows (per-second and per-2-minutes)."""

    def __init__(self, per_second: float, per_two_min: int):
        self.per_second = per_second
        self.per_two_min = per_two_min
        self.recent_1s: deque[float] = deque()
        self.recent_120s: deque[float] = deque()

    def wait(self) -> None:
        while True:
            now = time.monotonic()
            while self.recent_1s and now - self.recent_1s[0] > 1.0:
                self.recent_1s.popleft()
            while self.recent_120s and now - self.recent_120s[0] > 120.0:
                self.recent_120s.popleft()
            if (len(self.recent_1s) < self.per_second
                    and len(self.recent_120s) < self.per_two_min):
                self.recent_1s.append(now)
                self.recent_120s.append(now)
                return
            time.sleep(0.05)


class RiotClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.RIOT_API_KEY
        if not self.api_key:
            raise RuntimeError("RIOT_API_KEY is not set")
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key
        self.limiter = RateLimiter(config.REQUESTS_PER_SECOND,
                                   config.REQUESTS_PER_TWO_MIN)

    # ---------------- core request with retry ----------------
    def _get(self, url: str, params: dict | None = None, max_retries: int = 5):
        for attempt in range(max_retries):
            self.limiter.wait()
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                log.warning("429 rate limited; sleeping %ss (%s)", retry_after, url)
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt
                log.warning("%s from Riot; retrying in %ss", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Exceeded retries for {url}")

    # ---------------- League-V4 (platform routing) ----------------
    def apex_league(self, platform: str, tier_endpoint: str) -> dict | None:
        """tier_endpoint in {'challengerleagues','grandmasterleagues','masterleagues'}"""
        url = (f"https://{platform}.api.riotgames.com/lol/league/v4/"
               f"{tier_endpoint}/by-queue/RANKED_SOLO_5x5")
        return self._get(url)

    # ---------------- Summoner-V4 (platform routing) ----------------
    def summoner_by_id(self, platform: str, summoner_id: str) -> dict | None:
        url = (f"https://{platform}.api.riotgames.com/lol/summoner/v4/"
               f"summoners/{summoner_id}")
        return self._get(url)

    # ---------------- Match-V5 (regional routing) ----------------
    def match_ids_by_puuid(self, routing: str, puuid: str,
                           queue: int, count: int,
                           start_time: int | None = None) -> list[str]:
        url = (f"https://{routing}.api.riotgames.com/lol/match/v5/"
               f"matches/by-puuid/{puuid}/ids")
        params: dict = {"queue": queue, "count": count}
        if start_time:
            params["startTime"] = start_time
        return self._get(url, params=params) or []

    def match_detail(self, routing: str, match_id: str) -> dict | None:
        url = (f"https://{routing}.api.riotgames.com/lol/match/v5/"
               f"matches/{match_id}")
        return self._get(url)
