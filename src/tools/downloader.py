"""
Video downloader - downloads videos from various platforms
Production-grade with cookie rotation for 100s of concurrent downloads
"""

import os
import time
import random
from pathlib import Path
from typing import Optional
from src.core.models import VideoResult, Platform
from src.tools.cookie_manager import get_cookie_manager


class VideoDownloader:
    """
    Download videos from various platforms with production-grade features
    
    Features:
    - Automatic cookie rotation for YouTube
    - Retry logic with exponential backoff
    - Thread-safe for concurrent downloads
    - Health tracking and auto-recovery
    """
    
    def __init__(self, output_dir: str = "downloads"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Get cookie manager
        self.cookie_manager = get_cookie_manager()
        
        # Retry configuration
        self.max_retries = 3
        self.base_delay = 2  # seconds
    
    def download_youtube(self, video: VideoResult, output_name: Optional[str] = None) -> Optional[str]:
        """
        Download YouTube video using yt-dlp with cookie rotation
        
        Automatically retries with different cookies on failure
        """
        import yt_dlp
        
        if output_name is None:
            output_name = f"{video.id}.mp4"
        
        output_path = self.output_dir / output_name
        
        # Try download with cookie rotation
        for attempt in range(self.max_retries):
            # Get best available cookie
            cookie = self.cookie_manager.get_best_cookie()
            
            if cookie is None:
                print("❌ No cookies available - please add cookie files to cookies/ directory")
                return None
            
            try:
                print(f"📥 Downloading with {cookie.name} (attempt {attempt + 1}/{self.max_retries})...")
                
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'outtmpl': str(output_path),
                    'quiet': False,
                    'no_warnings': False,
                    
                    # CRITICAL: Enable Deno JS runtime to solve YouTube's JS challenges
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['tv_embedded', 'web'],
                            'player_skip': ['webpage', 'configs'],
                        }
                    },
                    
                    # Enable remote challenge solver scripts for Deno
                    'remote_components': ['ejs:github'],
                    
                    # Use cookies for authentication
                    'cookiefile': cookie.path if cookie else None,
                    
                    # Additional options to avoid detection
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'referer': 'https://www.youtube.com/',
                    
                    # Rate limiting
                    'sleep_interval': random.uniform(3, 6),
                    'max_sleep_interval': 10,
                    'sleep_interval_requests': 3,
                    
                    # Retry settings
                    'retries': 3,
                    'fragment_retries': 3,
                }
                
                # Don't use cookies with Android client (not supported)
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video.url])
                
                # Success!
                self.cookie_manager.report_success(cookie)
                print(f"✅ Downloaded: {output_name}")
                return str(output_path)
            
            except Exception as e:
                error_msg = str(e).lower()
                
                # Report failure
                self.cookie_manager.report_failure(cookie, str(e)[:50])
                
                # Check if it's a bot detection error
                if any(keyword in error_msg for keyword in ['sign in', 'bot', 'captcha', 'blocked', '403']):
                    print(f"🤖 Bot detected with {cookie.name}, rotating to next cookie...")
                else:
                    print(f"❌ Download error: {e}")
                
                # Exponential backoff before retry
                if attempt < self.max_retries - 1:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                    print(f"⏳ Waiting {delay:.1f}s before retry...")
                    time.sleep(delay)
        
        print(f"❌ Failed to download after {self.max_retries} attempts")
        return None
    
    def download(self, url: str, output_dir: Optional[str] = None) -> Optional[str]:
        """
        Download video from URL
        
        Args:
            url: Video URL
            output_dir: Optional output directory (overrides default)
        
        Returns:
            Path to downloaded video or None
        """
        import yt_dlp
        
        # Use custom output dir if provided
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
        else:
            output_path = self.output_dir
        
        # Try download with cookie rotation
        for attempt in range(self.max_retries):
            cookie = self.cookie_manager.get_best_cookie()
            
            if cookie is None:
                print("❌ No cookies available")
                # Try without cookies as fallback
                cookie_path = None
            else:
                cookie_path = cookie.path
            
            try:
                print(f"📥 Downloading from {url[:50]}... (attempt {attempt + 1}/{self.max_retries})")
                
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'outtmpl': str(output_path / '%(id)s.%(ext)s'),
                    'quiet': False,
                    'no_warnings': False,
                    
                    # CRITICAL: Enable Deno JS runtime to solve YouTube's JS challenges
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['tv_embedded', 'web'],
                            'player_skip': ['webpage', 'configs'],
                        }
                    },
                    
                    # Enable remote challenge solver scripts for Deno
                    'remote_components': ['ejs:github'],
                    
                    # Use cookies for authentication
                    'cookiefile': cookie_path if cookie_path else None,
                    
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'referer': 'https://www.youtube.com/',
                    'sleep_interval': random.uniform(3, 6),
                    'max_sleep_interval': 10,
                    'sleep_interval_requests': 3,
                    'sleep_interval_subtitles': 2,
                    'retries': 3,
                    'fragment_retries': 3,
                    'extractor_retries': 3,
                    'nocheckcertificate': True,
                    'prefer_insecure': False,
                    'http_chunk_size': 10485760,
                }
                
                # Don't use cookies with Android client (not supported)
                # if cookie_path:
                #     ydl_opts['cookiefile'] = cookie_path
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                
                # Success!
                if cookie:
                    self.cookie_manager.report_success(cookie)
                
                print(f"✅ Downloaded: {filename}")
                return filename
            
            except Exception as e:
                if cookie:
                    self.cookie_manager.report_failure(cookie, str(e)[:50])
                
                print(f"❌ Download error: {e}")
                
                if attempt < self.max_retries - 1:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(delay)
        
        return None
    
    def download_clip(self, url: str, start_time: float, duration: float, output_path: str) -> Optional[str]:
        """
        Download a specific clip from a video without downloading the full video
        Uses yt-dlp with ffmpeg to extract only the needed segment
        
        Args:
            url: Video URL
            start_time: Start time in seconds (random if 0)
            duration: Clip duration in seconds
            output_path: Full path for output file
        
        Returns:
            Path to extracted clip or None
        """
        import yt_dlp
        
        # Create output directory
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Try download with cookie rotation
        for attempt in range(self.max_retries):
            cookie = self.cookie_manager.get_best_cookie()
            
            if cookie is None:
                print("❌ No cookies available")
                cookie_path = None
            else:
                cookie_path = cookie.path
            
            try:
                print(f"✂️ Extracting {duration}s clip from {url[:50]}... (attempt {attempt + 1}/{self.max_retries})")
                
                # If start_time is 0, get video info first to pick random start
                if start_time == 0:
                    with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': cookie_path}) as ydl:
                        info = ydl.extract_info(url, download=False)
                        video_duration = info.get('duration', 60)
                        # Pick random start time, leaving room for the clip
                        max_start = max(0, video_duration - duration - 5)
                        start_time = random.uniform(0, max_start) if max_start > 0 else 0
                
                # Download only the specific segment
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'outtmpl': output_path,
                    'quiet': False,
                    'no_warnings': False,
                    
                    # CRITICAL: Enable Deno JS runtime
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['tv_embedded', 'web'],
                            'player_skip': ['webpage', 'configs'],
                        }
                    },
                    
                    # Enable remote challenge solver scripts for Deno
                    'remote_components': ['ejs:github'],
                    
                    # Use cookies
                    'cookiefile': cookie_path if cookie_path else None,
                    
                    # Extract specific segment using ffmpeg
                    'download_ranges': yt_dlp.utils.download_range_func(None, [(start_time, start_time + duration)]),
                    'force_keyframes_at_cuts': True,
                    
                    # Post-processing to ensure exact duration
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': 'mp4',
                    }],
                    
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'referer': 'https://www.youtube.com/',
                    'sleep_interval': random.uniform(3, 6),
                    'max_sleep_interval': 10,
                    'sleep_interval_requests': 3,
                    'retries': 3,
                    'fragment_retries': 3,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                # Success!
                if cookie:
                    self.cookie_manager.report_success(cookie)
                
                print(f"✅ Extracted clip: {output_path}")
                return output_path
            
            except Exception as e:
                if cookie:
                    self.cookie_manager.report_failure(cookie, str(e)[:50])
                
                print(f"❌ Clip extraction error: {e}")
                
                if attempt < self.max_retries - 1:
                    delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(delay)
        
        print(f"❌ Failed to extract clip after {self.max_retries} attempts")
        return None
    
    def get_stats(self) -> dict:
        """Get download statistics"""
        return self.cookie_manager.get_stats()
