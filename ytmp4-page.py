#!/usr/bin/env python3
from html import escape
from pathlib import Path
import sys
v=Path(sys.argv[1]).resolve();d=v.parent;s=v.stem
cover=next((p for p in (d/f'{s}.jpg',d/f'{s}.webp',d/f'{s}.png') if p.exists()),None)
if cover:
 t=escape(s);movie=escape(v.name,quote=True);img=escape(cover.name,quote=True)
 (d/'Accueil cinéma.html').write_text(f'''<!doctype html><html lang="fr"><meta charset="utf-8"><title>{t}</title><style>body{{margin:0;background:#090909;color:white;font:16px Arial}}main{{min-height:100vh;display:flex;align-items:end;padding:8vw;box-sizing:border-box;background:linear-gradient(90deg,#000e,transparent),linear-gradient(0deg,#090909,transparent 70%),url("{img}") center/cover}}h1{{font-size:clamp(2rem,7vw,6rem)}}a{{background:#e50914;color:white;padding:1rem;text-decoration:none}}</style><main><section><h1>{t}</h1><a href="{movie}">▶ Lire le film</a></section></main></html>''',encoding='utf-8')
