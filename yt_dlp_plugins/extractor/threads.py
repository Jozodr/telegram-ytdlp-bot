import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    determine_ext,
    int_or_none,
    remove_end,
    strip_or_none,
)
from yt_dlp.utils.traversal import traverse_obj


class ThreadsIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?threads\.(?:net|com)/(?:@[^/]+/)?(?:post|t)/(?P<id>[^/?#&]+)'
    _TESTS = [{
        'note': 'Post with single video',
        'url': 'https://www.threads.com/@zuck/post/DHV7vTivqWD',
        'info_dict': {
            'id': 'DHV7vTivqWD',
            'ext': 'mp4',
            'title': 'Me finding out Llama hit 1 BILLION downloads.',
            'description': 'Me finding out Llama hit 1 BILLION downloads.',
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
            'title': str,
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
    }]

    def _real_extract(self, url):
        post_id = self._match_id(url)
        webpage = self._download_webpage(url, post_id, note='Downloading post page')

        json_data = None

        json_scripts = re.findall(
            r'<script type="application/json"[^>]*?\sdata-sjs[^>]*?>(.*?)<\s*/script\s*>',
            webpage,
            re.DOTALL | re.IGNORECASE,
        )
        for script in json_scripts:
            if post_id not in script or 'RelayPrefetchedStreamCache' not in script:
                continue

            candidate_json = self._search_json(
                r'"result":', script, 'result data', post_id, fatal=False)

            if not candidate_json:
                continue

            post_data = traverse_obj(
                candidate_json, ('data', 'data', 'edges'))

            if post_data is not None:
                json_data = post_data
                break

        if not json_data:
            self.raise_no_formats(
                'Could not extract post data. The post may be private or deleted. '
                'You may need to log in.',
                expected=True,
            )

        main_post = None
        for node in json_data:
            for item in traverse_obj(node, ('node', 'thread_items'), default=[]):
                post_candidate = item.get('post')
                if traverse_obj(post_candidate, 'code') == post_id:
                    main_post = post_candidate
                    break
            if main_post:
                break

        if not main_post:
            self.raise_no_formats(
                'Could not find post data matching the post ID.', expected=True)

        uploader = traverse_obj(main_post, ('user', 'username'))
        caption = traverse_obj(main_post, ('caption', 'text'))
        title = (
            caption
            or strip_or_none(remove_end(
                self._html_extract_title(webpage), '• Threads'))
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

            # Video
            if media.get('video_versions'):
                formats = []
                for video in media.get('video_versions'):
                    formats.append({
                        'url': video.get('url'),
                        'width': int_or_none(video.get('width')),
                        'height': int_or_none(video.get('height')),
                    })

                playlist_entries.append({
                    'id': entry_id,
                    'title': title,
                    'formats': formats,
                    'thumbnail': traverse_obj(
                        media,
                        ('image_versions2', 'candidates', 0, 'url')),
                })
                continue

            # Image
            image_candidates = traverse_obj(
                media, ('image_versions2', 'candidates'))
            if image_candidates:
                best_image = image_candidates[0]
                playlist_entries.append({
                    'id': entry_id,
                    'title': title,
                    'formats': [{
                        'url': best_image.get('url'),
                        'ext': determine_ext(best_image.get('url'), 'jpg'),
                        'width': int_or_none(best_image.get('width')),
                        'height': int_or_none(best_image.get('height')),
                        'vcodec': 'none',
                    }],
                    'thumbnail': best_image.get('url'),
                })

        if not playlist_entries:
            self.raise_no_formats(
                'This post contains no downloadable video or images.',
                expected=True)

        if len(playlist_entries) == 1:
            return {**playlist_entries[0], **playlist_metadata}

        return self.playlist_result(
            playlist_entries, **playlist_metadata)
