# Builds the public website from the workspace: index.html is written as the body of a Claude artifact page, so for the web it
# gets a normal document wrapper (doctype, <head>, <body>). Everything else is copied as it is.
#   python tools/build_site.py <output folder>
import io, os, shutil, sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, '_site')
COPY = ['sw/engines.js', 'sw/daylight.js', 'sw/worker.js', 'map/vancouver.js', 'rhino/rhino3dm.min.js', 'rhino/rhino3dm.wasm', 'rhino/sw_link.py', 'link.js']

src = io.open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
cut = src.index('<div id="stage">')          # everything before the stage is head content: charset, title, fonts, styles
head, body = src[:cut], src[cut:]
doc = ('<!doctype html>\n<html lang="en">\n<head>\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
       '<meta name="description" content="Tower Workspace: site, massing, analysis nodes and drawing sets for early tower design, in one page.">\n'
       '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>\n'
       + head + '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
os.makedirs(OUT, exist_ok=True)
io.open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8', newline='\n').write(doc)
for rel in COPY:
    dst = os.path.join(OUT, *rel.split('/'))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, *rel.split('/')), dst)
io.open(os.path.join(OUT, '.nojekyll'), 'w').write('')
print('built', OUT, os.path.getsize(os.path.join(OUT, 'index.html')), 'bytes of index.html,', len(COPY), 'files copied')
