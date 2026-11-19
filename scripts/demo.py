#!/usr/bin/env python3
"""Serve the production UI with fictional fixtures for screenshots. No real library or account is read."""
from functools import partial
import http.server
import json
from pathlib import Path
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'PLFAUTEUILDEMO01'
TITLES = [('Horizon', 'horizon', 'Documentaires', 1680), ('Les heures bleues', 'bleu', 'Cinéma', 5400),
          ('Orbites', 'orbites', 'Sciences', 2400), ('Le grand dehors', 'dehors', 'Documentaires', 2100),
          ('À contre-jour', 'jour', 'Cinéma', 4200), ('Lignes de fuite', 'lignes', 'Voyages', 1800)]
ITEMS = [dict(id=f'demo{i:07d}', title=title, thumbnail=f'/demo-assets/{slug}.svg', duration=duration,
              channel='Studio Fauteuil', genre=genre, local=True, kept=False, downloaded=False,
              watchlisted=i in (0, 2, 4), added_at=1700000000+i,
              progress={'current':420, 'duration':duration, 'updated':1800000000} if i == 0 else None)
         for i, (title, slug, genre, duration) in enumerate(TITLES)]

class Demo(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path
        if route == '/api/library': return self.send_json(ITEMS)
        if route == '/api/profiles': return self.send_json({'active':'default','profiles':[{'id':'default','name':'Démo','avatar':'🍿'}]})
        if route == '/api/search-history': return self.send_json([])
        if route == '/api/google/suggestions': return self.send_json({'connected':False,'items':[],'notice':''})
        if route == '/api/google/trending': return self.send_json({'items':[]})
        if route == '/api/details':
            ident = urllib.parse.parse_qs(url.query).get('id',[''])[0]
            item = next((entry for entry in ITEMS if entry['id']==ident),ITEMS[0])
            return self.send_json(dict(item, description='Une bibliothèque de démonstration. Des paysages à parcourir, des histoires à retrouver, et le plaisir de prendre le temps.\n\nIllustration originale — Studio Fauteuil.'))
        if route == '/api/playlists':return self.send_json({'items':[{'id':COLLECTION,'title':'Prendre le large','thumbnail':ITEMS[0]['thumbnail'],'channel':'Bibliothèque de démonstration','total':6,'loaded_at':1}]})
        if route == '/api/playlist':return self.send_json({'id':COLLECTION,'title':'Prendre le large','description':'Six histoires, dans le bon ordre. Une collection de démonstration, prête à suivre le voyage.','total':6,'loaded_at':1,'filtered':0,'items':[dict(item,position=i,available=True,finished=False) for i,item in enumerate(ITEMS)]})
        if route == '/detail':self.path='/detail.html'
        return super().do_GET()

    def translate_path(self, path):
        path=urllib.parse.unquote(urllib.parse.urlparse(path).path)
        root=ROOT/'docs/assets/demo' if path.startswith('/demo-assets/') else ROOT/'app/web'
        relative=path.removeprefix('/demo-assets/') if path.startswith('/demo-assets/') else path.lstrip('/') or 'index.html'
        target=(root/relative).resolve()
        return str(target if target.is_relative_to(root.resolve()) else root/'.not-found')

if __name__=='__main__':
    server=http.server.ThreadingHTTPServer(('127.0.0.1',8767),Demo)
    print('Fictional demo UI: http://127.0.0.1:8767',flush=True)
    server.serve_forever()
