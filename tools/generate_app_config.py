"""Generate native constants from the single reviewed app address."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

def website_url(root=ROOT):
    value=json.loads((root/'app-config.json').read_text())['website_url']
    url=urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.port not in (None,443) or url.path not in ('','/') or url.query or url.fragment
            or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::443)?/?',value)):
        raise ValueError('website_url must be an HTTPS root address without credentials')
    return value.rstrip('/')+'/'

def generate(platform,root=ROOT):
    url=website_url(root)
    if platform=='ios':
        path=root/'ios/MySelfPay/AppConfig.swift'
        content='// Generated from app-config.json. Do not edit.\nimport Foundation\nenum AppConfig { static let home = URL(string: '+json.dumps(url)+')! }\n'
    elif platform=='android':
        path=root/'android/app/src/main/java/com/onesmarter/myselfpay/AppConfig.java'
        content='// Generated from app-config.json. Do not edit.\npackage com.onesmarter.myselfpay;\nfinal class AppConfig { static final String HOME = '+json.dumps(url)+'; }\n'
    else: raise ValueError('Unknown platform')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('platform',choices=['ios','android'])
    generate(parser.parse_args().platform)

