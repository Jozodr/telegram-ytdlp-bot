import html
import json
import logging
import os
import re
import subprocess

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

_SHARE_INFO_KEYS = ('quoted_attachment_post', 'quoted_post', 'reposted_post')


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
            'uploader_url': 'https://www.threads.net/@zuck',
            'channel': 'zuck',
            'channel_url': 'https://www.threads.net/@zuck',
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
            'uploader_url': 'https://www.threads.net/@zuck',
            'channel': 'zuck',
            'channel_url': 'https://www.threads.net/@zuck',
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
    }, {
        'note': 'Share shortlink resolving to a repost of a video',
        'url': 'https://www.threads.com/share/BAZGItvkRo/',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        post_id = self._match_id(url)
        webpage = self._download_webpage(url, post_id, note='Downloading post page')

        webpage_title = strip_or_none(
            remove_end(self._html_extract_title(webpage), '• Threads'))

        # Strategy 1: embedded JSON scraping (fast, works when the server
        # prefetches the post data into data-sjs script blocks)
        main_post = self._find_post_data(webpage, post_id)
        if main_post:
            return self._process_post(post_id, main_post, webpage, webpage_title)

        # Strategy 2: headless browser fallback. Threads now serves a JS-only
        # shell page for many posts; the data-sjs blocks appear after hydration.
        self.to_screen('HTML extraction failed, trying headless browser...')
        rendered_html = self._render_page(url, post_id)
        if rendered_html:
            main_post = self._find_post_data(rendered_html, post_id)
            if main_post:
                return self._process_post(
                    post_id, main_post, rendered_html, webpage_title)
            dom_result = self._extract_from_dom(
                rendered_html, post_id, webpage_title)
            if dom_result:
                return dom_result

        self.raise_no_formats(
            'Could not extract video or images from this post. The post may '
            'be private or contain no media.',
            expected=True)

    def _iter_result_objects(self, script):
        """Yield every JSON object following a "result": key in the script."""
        decoder = json.JSONDecoder()
        for match in re.finditer(r'"result"\s*:', script):
            try:
                obj, _ = decoder.raw_decode(script, match.end())
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj

    def _find_post_data(self, page, post_id):
        """Locate the post media payload inside embedded data-sjs JSON."""
        json_scripts = re.findall(
            r'<script[^>]*data-sjs[^>]*>(.*?)</script>',
            page, re.DOTALL | re.IGNORECASE,
        )

        for script in json_scripts:
            if post_id not in script or 'RelayPrefetchedStreamCache' not in script:
                continue
            for candidate in self._iter_result_objects(script):
                post = self._find_post_node(candidate, post_id)
                if post:
                    return post
        return None

    def _find_post_node(self, candidate, post_id):
        # Thread structure: data.data.edges[].node.thread_items[].post
        edges = traverse_obj(candidate, ('data', 'data', 'edges'))
        if edges:
            for node in edges:
                for item in traverse_obj(node, ('node', 'thread_items'), default=[]):
                    post = item.get('post') if isinstance(item, dict) else None
                    if isinstance(post, dict):
                        media_post = self._resolve_media_post(post, post_id)
                        if media_post:
                            return media_post

        # Single media API response: data.media
        media = traverse_obj(candidate, ('data', 'media'))
        if isinstance(media, dict):
            media_post = self._resolve_media_post(media, post_id)
            if media_post:
                return media_post
        return None

    @staticmethod
    def _has_media(post):
        return bool(
            post.get('video_versions')
            or traverse_obj(post, ('image_versions2', 'candidates'))
            or post.get('carousel_media'))

    def _resolve_media_post(self, post, post_id):
        """Return the post object carrying the media.

        Reposts/quotes have no media of their own; the actual video/image
        lives either in a nested share_info post (quoted_attachment_post,
        etc.) or under text_post_app_info.linked_inline_media.
        """
        if traverse_obj(post, 'code') != post_id:
            return None
        if self._has_media(post):
            return post

        linked = traverse_obj(
            post, ('text_post_app_info', 'linked_inline_media'))
        if isinstance(linked, dict) and self._has_media(linked):
            return linked

        nested = post
        for _ in range(2):
            share_info = traverse_obj(
                nested, ('text_post_app_info', 'share_info'))
            if not isinstance(share_info, dict):
                break
            found = None
            for key in _SHARE_INFO_KEYS:
                candidate = share_info.get(key)
                if isinstance(candidate, dict) and self._has_media(candidate):
                    return candidate
                if isinstance(candidate, dict):
                    found = candidate
            nested = found
            if not nested:
                break
        return None

    def _process_post(self, post_id, main_post, webpage, webpage_title):
        """Process a post with media data from JSON extraction."""
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
            'description': caption or self._og_search_description(
                webpage, default=None),
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

    def _render_page(self, url, post_id):
        """Render the page with headless Chromium and return the DOM HTML."""
        chromium_path = None
        for path in ('/usr/bin/chromium-browser', '/usr/bin/chromium', '/usr/bin/chrome'):
            if os.path.exists(path):
                chromium_path = path
                break

        if not chromium_path:
            self.to_screen('No headless browser available for this post.')
            return None

        self.to_screen(f'Using headless Chromium at {chromium_path}')

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
                timeout=60,
            )
            rendered_html = result.stdout
        except subprocess.TimeoutExpired:
            self.to_screen('Headless browser timed out rendering the page.')
            return None
        except Exception as e:
            self.to_screen(f'Headless browser error: {e}')
            return None

        if not rendered_html or len(rendered_html) < 1000:
            self.to_screen('Headless browser returned empty page.')
            return None

        self.to_screen(f'Rendered page: {len(rendered_html)} chars')
        return rendered_html

    def _extract_from_dom(self, rendered_html, post_id, webpage_title):
        """Last-resort regex scraping of media URLs from the rendered DOM."""
        # Undo JSON-style escaping so HTML entities and literal tags are
        # visible to the URL regexes below
        rendered_html = (
            rendered_html.replace('\\/', '/')
            .replace('\\u0026', '&')
            .replace('\\u003C', '<')
            .replace('\\u003c', '<')
            .replace('\\u003E', '>')
            .replace('\\u003e', '>'))

        video_urls = []

        def clean_url(candidate):
            candidate = html.unescape(candidate)
            # Cut trailing non-URL garbage (e.g. stray XML/DASH tags)
            candidate = re.split(
                r'[^A-Za-z0-9._~:/?#@!$&()*+,;=%-]', candidate, maxsplit=1)[0]
            cleaned = url_or_none(candidate)
            if not cleaned or 'rsrc.php' in cleaned:
                return None
            return cleaned

        def nearest_post_code(pos):
            """Post code of the closest preceding post link in the DOM."""
            window = rendered_html[max(0, pos - 20000):pos]
            codes = re.findall(r'/(?:post|t)/([\w-]+)', window)
            return codes[-1] if codes else None

        def add_url(candidate):
            cleaned = clean_url(candidate)
            if cleaned and cleaned not in video_urls:
                video_urls.append(cleaned)

        # Video elements rendered by the player. Threads post pages also
        # hydrate the following feed posts, so only videos whose nearest
        # preceding post link is the target post are considered "owned".
        owned_videos = []
        for pattern in (r'<video[^>]*src="([^"]+)"', r'<source[^>]*src="([^"]+)"'):
            for match in re.finditer(pattern, rendered_html):
                cleaned = clean_url(match.group(1))
                if not cleaned:
                    continue
                if nearest_post_code(match.start()) == post_id:
                    if cleaned not in owned_videos:
                        owned_videos.append(cleaned)
                elif cleaned not in video_urls:
                    video_urls.append(cleaned)

        # Prefer media that provably belongs to the target post
        if owned_videos:
            video_urls = owned_videos
        else:
            for match in re.finditer(r'"video_url"\s*:\s*"([^"]+)"', rendered_html):
                add_url(match.group(1))

            for match in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', rendered_html):
                candidate = html.unescape(match.group(1))
                if 'video' in candidate or '.mp4' in candidate:
                    add_url(candidate)

            for match in re.finditer(r'(https?://[^\s"\'<>\\]+\.mp4[^\s"\'<>\\]*)', rendered_html):
                add_url(match.group(1))

        title = webpage_title
        title_match = re.search(r'<title>([^<]+)</title>', rendered_html)
        if title_match:
            title = strip_or_none(
                remove_end(title_match.group(1), '• Threads')) or title

        if video_urls:
            self.to_screen(f'Found {len(video_urls)} video URL(s) via headless browser')
            return {
                'id': post_id,
                'title': title or f'Post {post_id}',
                'formats': [{'url': vurl} for vurl in video_urls],
            }

        image_urls = []
        for match in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', rendered_html):
            img_url = url_or_none(html.unescape(match.group(1)))
            if img_url and 'rsrc.php' not in img_url:
                image_urls.append(img_url)

        if image_urls:
            self.to_screen(f'Found {len(image_urls)} image URL(s) via headless browser')
            return {
                'id': post_id,
                'title': title or f'Post {post_id}',
                'url': image_urls[0],
                'ext': determine_ext(image_urls[0], 'jpg'),
                'vcodec': 'none',
            }

        return None
