import re
import json
import subprocess
import logging
import tempfile
import os

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    determine_ext,
    int_or_none,
    remove_end,
    strip_or_none,
    url_or_none,
)
from yt_dlp.utils.traversal import traverse_obj

logger = logging.getLogger(__name__)


class ThreadsIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?threads\.(?:net|com)/(?:@[^/]+/)?(?:post|t)/(?P<id>[^/?#&]+)'
    _TESTS = [{
        'note': 'Post with single video',
        'url': 'https://www.threads.com/@zuck/post/DHV7vTivqWD',
        'info_dict': {
            'id': 'DHV7vTivqWD',
            'ext': 'mp4',
            'title': 'Me finding out Llama hit 1 BILLION downloads.',
            'uploader': 'zuck',
            'uploader_id': '63055343223',
            'uploader_url': 'https://www.threads.com/@zuck',
            'channel': 'zuck',
            'channel_url': 'https://www.threads.com/@zuck',
            'channel_is_verified': True,
            'timestamp': 1742305717,
            'upload_date': '20250318',
            'like_count': int,
            'thumbnail': str,
        },
    }, {
        'note': 'Post with short URL (no username)',
        'url': 'https://www.threads.com/t/DHV7vTivqWD',
        'only_matching': True,
    }, {
        'note': 'Post with 1 image',
        'url': 'https://www.threads.com/@zuck/post/DI3mC0GxkYA',
        'info_dict': {
            'id': 'DI3mC0GxkYA',
            'ext': 'webp',
            'uploader': 'zuck',
            'uploader_id': '63055343223',
            'uploader_url': 'https://www.threads.com/@zuck',
            'channel': 'zuck',
            'channel_url': 'https://www.threads.com/@zuck',
            'channel_is_verified': True,
            'timestamp': 1745582191,
            'upload_date': '20250425',
            'like_count': int,
            'thumbnail': str,
        },
    }, {
        'note': 'Shared media post (requires headless browser)',
        'url': 'https://www.threads.com/@tinoh_79/post/DaheqevCK7_',
        'info_dict': {
            'id': 'DaheqevCK7_',
            'ext': 'mp4',
            'uploader': 'tinoh_79',
            'channel': 'tinoh_79',
        },
    }]

    def _real_extract(self, url):
        post_id = self._match_id(url)
        webpage = self._download_webpage(url, post_id, note='Downloading post page')

        main_post = None
        webpage_title = strip_or_none(
            remove_end(self._html_extract_title(webpage), '• Threads'))

        # Strategy 1: HTML scraping (fast, works for native posts)
        json_scripts = re.findall(
            r'<script[^>]*data-sjs[^>]*>(.*?)</script>',
            webpage, re.DOTALL | re.IGNORECASE,
        )

        for script in json_scripts:
            if post_id not in script or 'RelayPrefetchedStreamCache' not in script:
                continue
            candidate = self._search_json(
                r'"result":', script, 'result data', post_id, fatal=False)
            if not candidate:
                continue

            # Try data.data.edges (thread structure)
            edges = traverse_obj(candidate, ('data', 'data', 'edges'))
            if edges:
                for node in edges:
                    for item in traverse_obj(node, ('node', 'thread_items'), default=[]):
                        post = item.get('post')
                        if traverse_obj(post, 'code') == post_id:
                            video_versions = post.get('video_versions') or []
                            image_candidates = traverse_obj(
                                post, ('image_versions2', 'candidates')) or []
                            carousel = post.get('carousel_media') or []
                            if video_versions or image_candidates or carousel:
                                main_post = post
                                break
                    if main_post:
                        break

            # Try data.media (single media API response)
            if not main_post:
                media = traverse_obj(candidate, ('data', 'media'))
                if media and traverse_obj(media, 'code') == post_id:
                    video_versions = media.get('video_versions') or []
                    image_candidates = traverse_obj(
                        media, ('image_versions2', 'candidates')) or []
                    carousel = media.get('carousel_media') or []
                    if video_versions or image_candidates or carousel:
                        main_post = media

            if main_post:
                break

        # If HTML scraping found media, process it
        if main_post:
            return self._process_post(post_id, main_post, webpage, webpage_title)

        # Strategy 2: Headless browser fallback (for shared/embedded media)
        self.to_screen('HTML extraction failed, trying headless browser...')
        return self._extract_with_browser(url, post_id, webpage_title)

    def _process_post(self, post_id, main_post, webpage, webpage_title):
        """Process a post with media data from HTML extraction."""
        uploader = traverse_obj(main_post, ('user', 'username'))
        caption = traverse_obj(main_post, ('caption', 'text'))
        title = (
            caption
            or webpage_title
            or f'Post by {uploader}'
        )

        playlist_metadata = {
            'id': post_id,
            'title': title,
            'description': caption or self._og_search_description(webpage),
            'uploader': uploader,
            'uploader_id': traverse_obj(main_post, ('user', 'pk')),
            'uploader_url': f'https://www.threads.net/@{uploader}',
            'channel': uploader,
            'channel_url': f'https://www.threads.net/@{uploader}',
            'channel_is_verified': traverse_obj(
                main_post, ('user', 'is_verified')),
            'timestamp': int_or_none(main_post.get('taken_at')),
            'like_count': int_or_none(main_post.get('like_count')),
        }

        media_list = main_post.get('carousel_media') or [main_post]
        playlist_entries = []

        for i, media in enumerate(media_list):
            entry_id = (
                f'{post_id}_{i + 1}' if len(media_list) > 1 else post_id)

            video_versions = media.get('video_versions') or []
            if video_versions:
                formats = []
                for video in video_versions:
                    video_url = video.get('url')
                    if not video_url:
                        continue
                    formats.append({
                        'url': video_url,
                        'width': int_or_none(video.get('width')),
                        'height': int_or_none(video.get('height')),
                    })
                if formats:
                    playlist_entries.append({
                        'id': entry_id,
                        'title': title,
                        'formats': formats,
                        'thumbnail': traverse_obj(
                            media,
                            ('image_versions2', 'candidates', 0, 'url')),
                    })
                continue

            image_candidates = traverse_obj(
                media, ('image_versions2', 'candidates')) or []
            if image_candidates:
                best_image = image_candidates[0]
                img_url = best_image.get('url')
                if img_url:
                    playlist_entries.append({
                        'id': entry_id,
                        'title': title,
                        'formats': [{
                            'url': img_url,
                            'ext': determine_ext(img_url, 'jpg'),
                            'width': int_or_none(best_image.get('width')),
                            'height': int_or_none(best_image.get('height')),
                            'vcodec': 'none',
                        }],
                        'thumbnail': img_url,
                    })

        if not playlist_entries:
            self.raise_no_formats(
                'This post contains no downloadable video or images.',
                expected=True)

        if len(playlist_entries) == 1:
            return {**playlist_entries[0], **playlist_metadata}

        return self.playlist_result(playlist_entries, **playlist_metadata)

    def _extract_with_browser(self, url, post_id, webpage_title):
        """Use headless Chromium to extract video URLs from rendered page."""
        chromium_path = None
        for path in ['/usr/bin/chromium-browser', '/usr/bin/chromium', '/usr/bin/chrome']:
            if os.path.exists(path):
                chromium_path = path
                break

        if not chromium_path:
            self.raise_no_formats(
                'No headless browser available for shared media posts.',
                expected=True)

        self.to_screen(f'Using headless Chromium at {chromium_path}')

        # Use Chromium to dump the rendered DOM
        try:
            result = subprocess.run(
                [
                    chromium_path,
                    '--headless',
                    '--no-sandbox',
                    '--disable-gpu',
                    '--disable-dev-shm-usage',
                    '--dump-dom',
                    '--virtual-time-budget=10000',
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            rendered_html = result.stdout
        except subprocess.TimeoutExpired:
            self.raise_no_formats(
                'Headless browser timed out rendering the page.',
                expected=True)
        except Exception as e:
            self.raise_no_formats(
                f'Headless browser error: {e}',
                expected=True)

        if not rendered_html or len(rendered_html) < 1000:
            self.raise_no_formats(
                'Headless browser returned empty page.',
                expected=True)

        self.to_screen(f'Rendered page: {len(rendered_html)} chars')

        # Unescape JSON-style escaped slashes and unicode
        rendered_html = rendered_html.replace('\\/', '/').replace('\\u0026', '&')

        # Extract video URLs from rendered DOM
        video_urls = []
        # Look for video elements
        for match in re.finditer(r'<video[^>]*src="([^"]+)"', rendered_html):
            video_url = url_or_none(match.group(1))
            if video_url:
                video_urls.append(video_url)

        # Look for source elements inside video
        for match in re.finditer(r'<source[^>]*src="([^"]+)"', rendered_html):
            video_url = url_or_none(match.group(1))
            if video_url:
                video_urls.append(video_url)

        # Look for blob URLs (common for video players)
        # These won't work for download, but note them
        blob_urls = re.findall(r'src="(blob:https?://[^"]+)"', rendered_html)

        # Look for video URLs in JavaScript state
        for match in re.finditer(r'"video_url"\s*:\s*"([^"]+)"', rendered_html):
            raw_url = match.group(1).replace('\\u0026', '&')
            video_url = url_or_none(raw_url)
            if video_url and video_url not in video_urls:
                video_urls.append(video_url)

        # Look for display_url
        for match in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', rendered_html):
            raw_url = match.group(1).replace('\\u0026', '&')
            display_url = url_or_none(raw_url)
            if display_url and ('video' in display_url or '.mp4' in display_url):
                if display_url not in video_urls:
                    video_urls.append(display_url)

        # Look for any .mp4 URLs directly in the HTML
        for match in re.finditer(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', rendered_html):
            mp4_url = match.group(1)
            if mp4_url not in video_urls:
                video_urls.append(mp4_url)

        # Extract title from rendered page
        title = webpage_title
        title_match = re.search(r'<title>([^<]+)</title>', rendered_html)
        if title_match:
            title = strip_or_none(
                remove_end(title_match.group(1), '• Threads')) or title

        # Extract image URLs if no video found
        image_urls = []
        if not video_urls:
            for match in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', rendered_html):
                img_url = url_or_none(match.group(1).replace('\\u0026', '&'))
                if img_url:
                    image_urls.append(img_url)

        if video_urls:
            self.to_screen(f'Found {len(video_urls)} video URL(s) via headless browser')
            formats = [{'url': vurl} for vurl in video_urls]
            return {
                'id': post_id,
                'title': title or f'Post {post_id}',
                'formats': formats,
            }

        if image_urls:
            self.to_screen(f'Found {len(image_urls)} image URL(s) via headless browser')
            return {
                'id': post_id,
                'title': title or f'Post {post_id}',
                'url': image_urls[0],
                'ext': determine_ext(image_urls[0], 'jpg'),
                'vcodec': 'none',
            }

        self.raise_no_formats(
            'Could not extract video or images from this post even with '
            'headless browser. The post may be private or contain no media.',
            expected=True)
