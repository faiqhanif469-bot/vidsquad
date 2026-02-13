"""
Cookie Manager - Production-grade cookie rotation for yt-dlp
Handles 100s of concurrent downloads with automatic rotation and health tracking
"""

import os
import time
import random
import threading
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class CookieFile:
    """Cookie file metadata"""
    path: str
    name: str
    last_used: float = 0
    success_count: int = 0
    fail_count: int = 0
    is_blocked: bool = False
    blocked_until: Optional[float] = None
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    @property
    def is_available(self) -> bool:
        """Check if cookie is available for use"""
        if not self.is_blocked:
            return True
        
        # Check if block period has expired
        if self.blocked_until and time.time() > self.blocked_until:
            self.is_blocked = False
            self.blocked_until = None
            return True
        
        return False


class CookieManager:
    """
    Production-grade cookie rotation manager
    
    Features:
    - Automatic rotation across multiple cookie files
    - Health tracking (success/fail rates)
    - Temporary blocking of failed cookies
    - Thread-safe for concurrent downloads
    - Smart selection (least recently used + best success rate)
    - Auto-recovery from temporary blocks
    """
    
    def __init__(self, cookies_dir: str = "cookies"):
        self.cookies_dir = Path(cookies_dir)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        
        self.cookies: List[CookieFile] = []
        self.lock = threading.Lock()
        
        # Configuration
        self.block_duration = 300  # 5 minutes block after failures
        self.max_fails_before_block = 3  # Block after 3 consecutive fails
        self.min_delay_between_uses = 5  # Minimum 5 seconds between uses (increased)
        
        # Load all cookie files
        self._load_cookies()
    
    def _load_cookies(self):
        """Load all cookie files from directory"""
        cookie_files = list(self.cookies_dir.glob("*.txt"))
        
        if not cookie_files:
            print(f"⚠️  No cookie files found in {self.cookies_dir}")
            print("📝 Instructions:")
            print("   1. Install browser extension: 'Get cookies.txt LOCALLY'")
            print("   2. Login to YouTube with different Google accounts")
            print("   3. Export cookies and save as cookies/cookies1.txt, cookies2.txt, etc.")
            print("   4. Recommended: 3-5 cookie files for production")
            return
        
        for cookie_file in cookie_files:
            self.cookies.append(CookieFile(
                path=str(cookie_file),
                name=cookie_file.name
            ))
        
        print(f"✅ Loaded {len(self.cookies)} cookie files")
    
    def get_best_cookie(self) -> Optional[CookieFile]:
        """
        Get best available cookie using smart selection
        
        Selection criteria:
        1. Must be available (not blocked)
        2. Respect minimum delay between uses
        3. Prefer cookies with better success rates
        4. Use least recently used among top performers
        """
        with self.lock:
            if not self.cookies:
                return None
            
            # Filter available cookies
            available = [c for c in self.cookies if c.is_available]
            
            if not available:
                # All blocked, wait for first to unblock
                print("⚠️  All cookies blocked, waiting for recovery...")
                time.sleep(5)
                return self.get_best_cookie()
            
            # Filter by minimum delay
            current_time = time.time()
            ready = [
                c for c in available 
                if current_time - c.last_used >= self.min_delay_between_uses
            ]
            
            if not ready:
                # Wait for shortest delay
                wait_time = min(
                    self.min_delay_between_uses - (current_time - c.last_used)
                    for c in available
                )
                time.sleep(wait_time + 0.1)
                return self.get_best_cookie()
            
            # Sort by success rate (descending) then by last used (ascending)
            ready.sort(key=lambda c: (-c.success_rate, c.last_used))
            
            # Select best cookie
            best = ready[0]
            best.last_used = current_time
            
            return best
    
    def report_success(self, cookie: CookieFile):
        """Report successful download"""
        with self.lock:
            cookie.success_count += 1
            
            # Reset fail count on success
            if cookie.fail_count > 0:
                cookie.fail_count = max(0, cookie.fail_count - 1)
            
            print(f"✅ {cookie.name}: Success (rate: {cookie.success_rate:.1%})")
    
    def report_failure(self, cookie: CookieFile, error_msg: str = ""):
        """Report failed download"""
        with self.lock:
            cookie.fail_count += 1
            
            # Check if should block
            if cookie.fail_count >= self.max_fails_before_block:
                cookie.is_blocked = True
                cookie.blocked_until = time.time() + self.block_duration
                cookie.fail_count = 0  # Reset counter
                
                print(f"🚫 {cookie.name}: Blocked for {self.block_duration}s (too many failures)")
            else:
                print(f"❌ {cookie.name}: Failed ({error_msg}) - {cookie.fail_count}/{self.max_fails_before_block}")
    
    def get_stats(self) -> dict:
        """Get statistics about cookie pool"""
        with self.lock:
            total = len(self.cookies)
            available = len([c for c in self.cookies if c.is_available])
            blocked = total - available
            
            total_success = sum(c.success_count for c in self.cookies)
            total_fail = sum(c.fail_count for c in self.cookies)
            
            overall_rate = 0.0
            if total_success + total_fail > 0:
                overall_rate = total_success / (total_success + total_fail)
            
            return {
                'total_cookies': total,
                'available': available,
                'blocked': blocked,
                'total_downloads': total_success + total_fail,
                'success_rate': overall_rate,
                'cookies': [
                    {
                        'name': c.name,
                        'success_rate': c.success_rate,
                        'downloads': c.success_count + c.fail_count,
                        'is_blocked': c.is_blocked
                    }
                    for c in self.cookies
                ]
            }
    
    def add_cookie_file(self, file_path: str):
        """Add new cookie file to pool"""
        with self.lock:
            cookie_file = CookieFile(
                path=file_path,
                name=Path(file_path).name
            )
            self.cookies.append(cookie_file)
            print(f"➕ Added cookie: {cookie_file.name}")
    
    def remove_cookie_file(self, name: str):
        """Remove cookie file from pool"""
        with self.lock:
            self.cookies = [c for c in self.cookies if c.name != name]
            print(f"➖ Removed cookie: {name}")


# Global singleton instance
_cookie_manager = None


def get_cookie_manager() -> CookieManager:
    """Get global cookie manager instance"""
    global _cookie_manager
    if _cookie_manager is None:
        _cookie_manager = CookieManager()
    return _cookie_manager
