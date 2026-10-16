"""Merge authorized indexes and copy assets into portable shared storage."""
import argparse
import json
from pathlib import Path

from emoji_library import EmojiLibrary


def migrate(root):
    root = Path(root)
    cfg = json.loads((root / 'settings.json').read_text(encoding='utf-8-sig'))
    library = EmojiLibrary(root / 'shared-emoji-library', root / 'face-catalog.json', shared=True)
    reports = {}
    for group in cfg.get('chat_group_whitelist', [cfg['group_id']]):
        runtime = 'runtime' if int(group) == int(cfg['group_id']) else 'runtime-' + str(group)
        reports[str(group)] = library.import_library(root / runtime / 'emoji-library')
    reports['unique_stickers'] = sum(item.get('kind') == 'sticker' for item in library.items.values())
    reports['native_faces'] = sum(item.get('kind') == 'market_face' for item in library.items.values())
    reports['known_hashes'] = len(library.hashes)
    reports['assets'] = library.ensure_assets()
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    print(json.dumps(migrate(parser.parse_args().root), ensure_ascii=False))
