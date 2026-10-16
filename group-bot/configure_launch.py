"""Validate launch choices against the saved Codex model catalog, then save atomically."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile


def validate(catalog, model, effort, fast):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', model):
        raise ValueError('Invalid model name')
    entry = next((m for m in catalog['models'] if m.get('slug') == model), None)
    if not entry:
        raise ValueError('Model is absent from the current Codex catalog: ' + model)
    levels = [x['effort'] for x in entry.get('supported_reasoning_levels', [])]
    if effort not in levels:
        raise ValueError('Unsupported reasoning effort; choose: ' + ', '.join(levels))
    tiers = entry.get('additional_speed_tiers', [])
    if fast and 'fast' not in tiers and not any(t.get('id') in {'fast', 'priority'} for t in entry.get('service_tiers', [])):
        raise ValueError('This model does not advertise Fast mode')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', required=True)
    parser.add_argument('--fast', choices=['on', 'off'], required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    codex_dir = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    try:
        catalog = json.loads((codex_dir / 'models_cache.json').read_text(encoding='utf-8-sig'))
        validate(catalog, args.model, args.effort, args.fast == 'on')
        if not args.check:
            path = Path(__file__).with_name('settings.json')
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            config.update(model=args.model, reasoning_effort=args.effort, fast_mode=args.fast == 'on')
            # Unique sibling file avoids partially read settings and collisions between launchers.
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                    prefix='launch-', suffix='.tmp', delete=False) as stream:
                temp = Path(stream.name)
                json.dump(config, stream, ensure_ascii=False, indent=2)
            try:
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        print(f'Model: {args.model}; reasoning: {args.effort}; Fast: {args.fast}')
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
