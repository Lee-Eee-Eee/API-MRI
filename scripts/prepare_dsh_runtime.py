"""Bind the DSH package to the exact portable ZIP built and checked by CI."""
from pathlib import Path
import argparse
import hashlib
import json
import tomllib

ROOT=Path(__file__).resolve().parents[1]
def prepare(folder: Path) -> dict:
    version=tomllib.loads((ROOT/'pyproject.toml').read_text('utf8'))['project']['version']
    name=f'API-MRI-{version}-windows-x64.zip'
    archive=folder/name
    with archive.open('rb') as stream:
        raw_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    spec={'version':version,'url':f'https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v{version}/{name}',
        'sha256':raw_hash,'size_bytes':archive.stat().st_size}
    text=json.dumps(spec,indent=2)+'\n'
    (ROOT/'dsh-plugin/lib/runtime-manifest.json').write_text(text,encoding='utf8')
    (folder/'api-mri-runtime.json').write_text(text,encoding='utf8')
    return spec
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--release-dir',type=Path,required=True)
    print(json.dumps(prepare(parser.parse_args().release_dir)))
