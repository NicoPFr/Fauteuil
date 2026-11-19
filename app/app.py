#!/usr/bin/env python3
import http.server,json,subprocess,threading,webbrowser,urllib.parse,os,re,time,sqlite3,pathlib,secrets,hashlib,base64,urllib.error,urllib.request,urllib.parse as up,shutil,math,signal,stat,errno,ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import tempfile
os.environ['PATH']='/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:'+os.environ.get('PATH','')
ROOT=pathlib.Path(__file__).resolve().parent; MOVIES=pathlib.Path.home()/'Movies/Youtube/Movies'; DATA=pathlib.Path.home()/'Movies/Youtube/Cinema'; DATA.mkdir(parents=True,exist_ok=True); DB=DATA/'catalogue.sqlite3'; PORT=8765;  OAUTH_REDIRECT=f'http://127.0.0.1:{PORT}'
oauth_lock=threading.Lock()
google_refresh_lock=threading.Lock()
oauth_attempt={}
OAUTH_TTL=240
GOOGLE_RECONNECT_MESSAGE='Ton autorisation Google a expiré ou a été retirée. Reconnecte ton compte pour revoir tes abonnements.'
VIDEO_CONTENT_TYPES={
 '.mp4':'video/mp4',
 '.m4v':'video/x-m4v',
 '.mov':'video/quicktime',
 '.webm':'video/webm',
}
LOCAL_VIDEO_SUFFIXES=VIDEO_CONTENT_TYPES.keys()
with sqlite3.connect(DB) as c:
 c.execute('create table if not exists videos(id text primary key,title text,thumb text,duration integer,channel text)')
 if 'genre' not in {row[1] for row in c.execute('pragma table_info(videos)')}:
  c.execute("alter table videos add column genre text not null default ''")
 c.execute('create table if not exists video_synopses(id text primary key, description text, fetched_at real)')
 if 'youtube_state' not in {row[1] for row in c.execute('pragma table_info(video_synopses)')}:
  c.execute("alter table video_synopses add column youtube_state text not null default ''")
 c.execute('create table if not exists watchlist(id text primary key,title text,thumb text,duration integer,channel text,added real)')
 c.execute('create table if not exists playback(id text primary key,path text,current real,duration real,updated real)')
 c.execute('create table if not exists settings(key text primary key,value text)')
 c.execute('create table if not exists profiles(id text primary key,name text,avatar text)')
 c.execute('create table if not exists watchlist_profile(profile text,id text,title text,thumb text,duration integer,channel text,added real,primary key(profile,id))')
 c.execute('create table if not exists playback_profile(profile text,id text,path text,current real,duration real,updated real,primary key(profile,id))')
 c.execute('create table if not exists completed_playback(profile text,id text,finished_at real,primary key(profile,id))')
 c.execute('create table if not exists temporary_downloads(id text primary key,path text,created real)')
 c.execute('create table if not exists pending_playback_cleanup(id text,path text,primary key(id,path))')
 if 'kept' not in {row[1] for row in c.execute('pragma table_info(temporary_downloads)')}:
  c.execute('alter table temporary_downloads add column kept integer not null default 0')
 c.execute('create table if not exists imported_videos(path text primary key, added_at real)')
 c.execute('create table if not exists local_metadata(path text primary key,mtime real,size integer,duration real,width integer,height integer)')
 c.execute('create table if not exists recent_searches(profile text, term_key text, term text, video_ids text, searched_at real, primary key(profile,term_key))')
 if 'tags' not in {row[1] for row in c.execute('pragma table_info(local_metadata)')}:
  c.execute('alter table local_metadata add column tags text')
 c.execute("insert or ignore into profiles values('default','Principal','🍿')")
 if not c.execute("select 1 from settings where key='profile_migration_done'").fetchone():
  c.execute("insert or ignore into watchlist_profile select 'default',id,title,thumb,duration,channel,added from watchlist")
  c.execute("insert or ignore into playback_profile select 'default',id,path,current,duration,updated from playback")
  c.execute("insert into settings values('profile_migration_done','1')")
with sqlite3.connect(DB) as c:
 c.execute('create table if not exists youtube_playlists(profile text,id text,title text,description text,thumbnail text,channel text,total integer,loaded_at real not null default 0,synced_at real,primary key(profile,id))')
 c.execute('create table if not exists youtube_playlist_items(profile text,playlist text,position integer,video text,primary key(profile,playlist,position))')

def active_profile():
 rows=db("select value from settings where key='active_profile'")
 return rows[0]['value'] if rows else 'default'
def db(q,args=(),write=False):
 c=sqlite3.connect(DB)
 try:
  with c:
   c.row_factory=sqlite3.Row
   cur=c.execute(q,args)
   return cur.fetchall() if not write else None
 finally:c.close()
def video_metadata(path):
 path=pathlib.Path(path).resolve()
 info={'duration':0.0,'width':0,'height':0,'tags':{}}
 try:
  stat=path.stat()
 except OSError:
  return info
 rows=db('select mtime,size,duration,width,height,tags from local_metadata where path=?',(str(path),))
 if rows:
  cached=dict(rows[0])
  if cached['mtime']==stat.st_mtime and cached['size']==stat.st_size and cached['tags'] is not None:
   cached['tags']=json.loads(cached['tags'])
   if 'genre' in cached['tags']:return cached
 try:
  command=['ffprobe','-v','error','-show_entries',
           'format=duration:format_tags=artist,description,synopsis,comment,genre:stream=codec_type,width,height:stream_disposition=attached_pic',
           '-of','json',str(path)]
  data=json.loads(subprocess.check_output(command,stderr=subprocess.DEVNULL,text=True,timeout=20))
  container=data.get('format',{})
  info['duration']=float(container.get('duration') or 0)
  info['tags']=container.get('tags',{})
  info['tags'].setdefault('genre','')
  video=next((stream for stream in data.get('streams',[])
              if stream.get('codec_type')=='video' and not stream.get('disposition',{}).get('attached_pic')), {})
  info['width']=int(video.get('width') or 0)
  info['height']=int(video.get('height') or 0)
 except (OSError,ValueError,subprocess.SubprocessError):
  return info
 db("""insert or replace into local_metadata(path,mtime,size,duration,width,height,tags)
       values(?,?,?,?,?,?,?)""",
    (str(path),stat.st_mtime,stat.st_size,info['duration'],info['width'],info['height'],json.dumps(info['tags'])),write=True)
 return info

def youtube_link(value):
 try:
  url=urllib.parse.urlparse(value.strip())
  if url.scheme not in ('http','https'):return None,None
  host=url.hostname
 except ValueError:
  return None,None
 if host not in ('www.youtube.com','youtube.com','m.youtube.com','youtu.be'):
  return None,None
 path=url.path.rstrip('/')
 if path.startswith('/shorts/'):
  return None,'Les YouTube Shorts sont masqués dans Fauteuil.'
 if host=='youtu.be':
  video_id=path.lstrip('/')
 elif path=='/watch':
  video_id=urllib.parse.parse_qs(url.query).get('v',[''])[0]
 else:
  video_id=''
 if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
  return None,'Colle un lien vers une vidéo YouTube individuelle.'
 return video_id,None

def embedded_youtube_id(tags):
 return youtube_link(tags.get('comment',''))[0]

def local_youtube_file(video_id):
 for path,_,_ in local_catalogue():
  if embedded_youtube_id(video_metadata(path)['tags'])==video_id:
   return path
 return None

GENRE_LABELS={
 'film & animation':'Cinéma et animation','entertainment':'Divertissement',
 'music':'Musique','sports':'Sport','gaming':'Jeux vidéo','education':'Éducation',
 'science & technology':'Science et technologie','people & blogs':'Vlogs',
}

def genre_label(value):
 if not isinstance(value,str):return ''
 value=value.strip()[:80]
 return GENRE_LABELS.get(value.casefold(),value)

def add(items):
 with sqlite3.connect(DB) as connection:
  connection.executemany(
   """insert into videos(id,title,thumb,duration,channel,genre) values(?,?,?,?,?,?)
      on conflict(id) do update set title=excluded.title, thumb=excluded.thumb,
      duration=excluded.duration, channel=excluded.channel,
      genre=coalesce(nullif(excluded.genre,''),videos.genre)""",
   [(item['id'],item.get('title','Vidéo'),item.get('thumbnail',''),item.get('duration') or 0,
     item.get('channel','YouTube'),genre_label(item.get('genre'))) for item in items])
def remember_search(profile, term, items):
 ids=list(dict.fromkeys(video['id'] for video in items))
 if not ids:return
 with sqlite3.connect(DB) as connection:
  if not connection.execute('select 1 from profiles where id=?',(profile,)).fetchone():return
  connection.execute('insert or replace into recent_searches values(?,?,?,?,?)',
   (profile,term.casefold(),term,json.dumps(ids),time.time()))
  connection.execute('''delete from recent_searches where profile=? and term_key not in
   (select term_key from recent_searches where profile=? order by searched_at desc limit 6)''',(profile,profile))

def recent_searches(profile):
 rows=db('select term,video_ids from recent_searches where profile=? order by searched_at desc',(profile,))
 return [{'term':row['term'],'ids':json.loads(row['video_ids'])} for row in rows]
def iso_duration(value):
 m=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?',value or '')
 return int(m.group(1) or 0)*3600+int(m.group(2) or 0)*60+int(m.group(3) or 0) if m else 0
def natural_key(value):
 import unicodedata
 return [int(z) if z.isdigit() else z.lower() for z in re.split(r'(\d+)',unicodedata.normalize('NFKD',str(value)))]
def is_local_video(path):
 if path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES or not path.is_file():
  return False
 return MOVIES.resolve() in path.parents or bool(db('select 1 from imported_videos where path=?',(str(path),)))

def register_local_videos(paths):
 library=MOVIES.resolve()
 protected=DATA.resolve()
 added=0
 skipped=0
 with sqlite3.connect(DB) as connection:
  for value in paths:
   try:
    if not isinstance(value,str) or not pathlib.Path(value).is_absolute():raise ValueError
    path=pathlib.Path(value).resolve(strict=True)
    if (protected in path.parents or library in path.parents or
        not path.is_file() or path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES):
     skipped+=1
     continue
    with path.open('rb'):
     pass
   except (OSError,ValueError,RuntimeError):
    skipped+=1
    continue
   result=connection.execute('insert or ignore into imported_videos values(?,?)',(str(path),time.time()))
   added+=result.rowcount
   skipped+=1-result.rowcount
 return {'ok':True,'added':added,'skipped':skipped}

def imported_entries():
 for row in db('select path from imported_videos order by added_at desc'):
  path=pathlib.Path(row['path'])
  try:
   available=path.resolve(strict=True)==path and path.is_file()
  except (OSError,RuntimeError):
   available=False
  yield path,available

def episode_number(path, *, allow_bare_number=False):
 stem=path.stem.replace('_',' ').strip()
 match=re.search(r'\b(?:[ÉE]pisode\s*|S\d{1,2}[ .-]*E)(\d{1,3})\b',stem,re.I)
 if not match and allow_bare_number:
  match=re.match(r'^\s*(?:Ep?\s*)?(\d{1,3})(?=\s*[-. ]|$)',stem,re.I)
 return int(match.group(1)) if match else None

def episode_order(entry):
 path,season=entry
 episode=episode_number(path,allow_bare_number=True)
 return season or 1,episode if episode is not None else float('inf'),natural_key(path.stem)

def imported_series(path):
 folder_season=re.fullmatch(r'(?:season|saison|s)[ ._-]*(\d+)',path.parent.name,re.I)
 filename_season=re.search(r'\bS(\d{1,2})[ .-]*E\d+',path.stem.replace('_',' '),re.I)
 series_root=path.parent.parent if folder_season else path.parent
 home=pathlib.Path.home().resolve()
 standard_folders={home,home/'Desktop',home/'Downloads',home/'Documents',home/'Movies'}
 if series_root in standard_folders or series_root==series_root.parent:return None
 if not (folder_season or episode_number(path) is not None):return None
 season=folder_season or filename_season
 return series_root,int(season.group(1)) if season else 1

def local_catalogue():
 library=MOVIES.resolve()
 folders={}
 films=[]
 for path in sorted(library.rglob('*'),key=lambda p:natural_key(p.as_posix())):
  if path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES or not path.is_file() or path.is_symlink():
   continue
  if path.parent==library:
   films.append((path,None,None))
   continue
  folder_season=re.fullmatch(r'(?:season|saison|s)[ ._-]*(\d+)',path.parent.name,re.I)
  if path.parent.parent==library:
   folder_season=None
  filename_season=re.search(r'\bS(\d{1,2})[ .-]*E\d+',path.stem.replace('_',' '),re.I)
  season=folder_season or filename_season
  series_root=path.parent.parent if folder_season else path.parent
  folders.setdefault(series_root,[]).append((path,int(season.group(1)) if season else None))
 for path,available in imported_entries():
  if not available or library in path.parents:continue
  group=imported_series(path)
  if group:
   root,season=group
   folders.setdefault(root,[]).append((path,season))
  else:
   films.append((path,None,None))
 for root, entries in folders.items():
  if len(entries)==1 and entries[0][1] is None:
   films.append((entries[0][0],None,None))
   continue
  for path, season in sorted(entries,key=episode_order):
   films.append((path,root,season or 1))
 return films

def series_identifier(root):
 library=MOVIES.resolve()
 if root.is_relative_to(library):return str(root.relative_to(library))
 return 'imported:'+hashlib.sha256(os.fsencode(root)).hexdigest()

def display_title(path):
 path=pathlib.Path(path)
 stem=path.stem.replace('_',' ').strip()
 episode=episode_number(path,allow_bare_number=True)
 if episode is not None:return 'Épisode '+str(episode).zfill(2)
 stem=re.sub(r'\s*[\[(](?:[^\])]*(?:sub|subtitle|1080p|720p|4k|fr|en)[^\])]*)[\])]\s*$','',stem,flags=re.I)
 stem=re.sub(r'^\s*\d{1,3}\s*[-_.]\s*','',stem).strip(' -_.')
 return stem or pathlib.Path(path).stem
cover_generation=threading.Lock()

def local_cover_url(path):
 return '/api/thumb?'+up.urlencode({'video':str(path)})

def local_cover(path):
 for suffix in ('.jpg','.png','.webp','.jpeg'):
  sidecar=path.with_suffix(suffix)
  if sidecar.is_file() and not sidecar.is_symlink():return sidecar
 cache=DATA/'jaquettes'
 if cache.is_symlink():return None
 cover=cache/(hashlib.sha1(str(path).encode()).hexdigest()+'.jpg')
 if regular_file_size(cover):return cover
 with cover_generation:
  if regular_file_size(cover):return cover
  try:
   if cover.is_file() and not cover.is_symlink() and time.time()-cover.stat().st_mtime<300:return None
   duration=video_metadata(path)['duration']
   at=max(0,min(duration*.35,duration-.1))
   cache.mkdir(exist_ok=True)
   with tempfile.TemporaryDirectory(dir=cache) as work:
    image=pathlib.Path(work)/'frame.jpg'
    try:
     result=subprocess.run(['ffmpeg','-y','-ss',str(at),'-i',str(path),'-map','0:V:0',
                           '-frames:v','1','-vf','scale=480:-2',str(image)],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
     success=result.returncode==0 and bool(regular_file_size(image))
    except (OSError,subprocess.TimeoutExpired):
     success=False
    if not success:image.write_bytes(b'')
    image.replace(cover)
    return cover if success else None
  except OSError:
   return None
def keychain(action,value=None,name='google-refresh'):
 service='io.github.nicopfr.fauteuil.'+name
 cmd=['security','find-generic-password','-s',service,'-w'] if action=='get' else (['security','delete-generic-password','-s',service] if action=='delete' else ['security','add-generic-password','-U','-s',service,'-a','cinema','-w',value or ''])
 try:
  result=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
  if action=='get':return result.stdout.strip() if result.returncode==0 else None
  return result.returncode==0
 except (OSError,subprocess.TimeoutExpired):
  return None
@lru_cache(maxsize=1)
def google_ssl_context():
 context=ssl.create_default_context()
 if not context.get_ca_certs():
  try:
   import certifi
  except ImportError:
   return context
  context.load_verify_locations(cafile=certifi.where())
 return context

def google_access_token():
 with google_refresh_lock:
  with oauth_lock:
   rows=db("select key,value from settings where key in ('google_access','google_expiry','google_client_id','google_reconnect')")
   settings={row['key']:row['value'] for row in rows}
   if settings.get('google_reconnect'):return None
   if settings.get('google_access') and float(settings.get('google_expiry',0))>time.time()+60:
    return settings['google_access']
   client_id=settings.get('google_client_id')
   if not client_id:return None
   refresh=keychain('get')
   secret=keychain('get',name='google-client-secret')
  if not refresh:return None
  form={'client_id':client_id,'refresh_token':refresh,'grant_type':'refresh_token'}
  if secret:form['client_secret']=secret
  revoked=False
  try:
   req=urllib.request.Request('https://oauth2.googleapis.com/token',data=up.urlencode(form).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
   with urllib.request.urlopen(req,timeout=20,context=google_ssl_context()) as response:
    token=json.load(response)
   access=token['access_token']
   expiry=time.time()+int(token.get('expires_in',3600))
  except urllib.error.HTTPError as error:
   with error:
    try:
     revoked=error.code==400 and json.load(error).get('error')=='invalid_grant'
    except (AttributeError,OSError,ValueError):
     pass
   if not revoked:return None
  except (OSError,ValueError,KeyError,TypeError):
   return None
  with oauth_lock:
   # An authorization or disconnection may have completed during the request.
   if refresh!=keychain('get'):return None
   with sqlite3.connect(DB) as c:
    if revoked:
     c.execute("delete from settings where key in ('google_access','google_expiry')")
     c.execute("insert or replace into settings values('google_reconnect','1')")
     keychain('delete')
     return None
    c.executemany('insert or replace into settings values(?,?)',[
     ('google_access',access),('google_expiry',str(expiry)),
    ])
   return access
def gapi(path,params,token):
 url='https://www.googleapis.com/youtube/v3/'+path+'?'+up.urlencode(params)
 req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
 with urllib.request.urlopen(req,timeout=25,context=google_ssl_context()) as response:
  return json.load(response)
playlist_lock=threading.Lock()

def playlist_id(value):
 if not isinstance(value,str):return None
 try:
  parsed=up.urlparse(value.strip())
  if parsed.scheme:
   if parsed.scheme not in ('http','https') or parsed.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):return None
   value=up.parse_qs(parsed.query).get('list',[''])[0]
 except ValueError:return None
 return value if re.fullmatch(r'[A-Za-z0-9_-]{10,120}',value) else None

def playlist_summary(row):
 sn=row.get('snippet',{})
 thumbs=sn.get('thumbnails',{})
 return {'id':row['id'],'title':sn.get('title','Playlist'),'description':sn.get('description',''),
         'thumbnail':thumbs.get('high',thumbs.get('medium',thumbs.get('default',{}))).get('url',''),
         'channel':sn.get('channelTitle','YouTube'),'total':row.get('contentDetails',{}).get('itemCount',0)}

def save_playlist(profile, item):
 db("""insert into youtube_playlists(profile,id,title,description,thumbnail,channel,total,loaded_at,synced_at)
       values(?,?,?,?,?,?,?,0,?) on conflict(profile,id) do update set title=excluded.title,
       description=excluded.description,thumbnail=excluded.thumbnail,channel=excluded.channel,total=excluded.total,synced_at=excluded.synced_at""",
    (profile,item['id'],item['title'],item.get('description',''),item.get('thumbnail',''),item.get('channel','YouTube'),item.get('total',0),time.time()),write=True)

def sync_playlists(profile):
 try:
  token=google_access_token()
  if not token:return {'ok':False,'error':'Connecte ton compte Google pour retrouver tes playlists.'}
  page='';count=0
  while True:
   response=gapi('playlists',{'part':'snippet,contentDetails','mine':'true','maxResults':50,'pageToken':page},token)
   for row in response.get('items',[]):
    save_playlist(profile,playlist_summary(row));count+=1
   page=response.get('nextPageToken','')
   if not page:break
  return {'ok':True,'count':count}
 except (OSError,ValueError):return {'ok':False,'error':'YouTube n’a pas pu actualiser tes playlists. Les collections déjà enregistrées restent disponibles.'}

def playlist_entries(profile, ident):
 rows=db('select * from youtube_playlists where profile=? and id=?',(profile,ident))
 if not rows:return None
 result=dict(rows[0]);result.pop('profile',None)
 entries=[dict(row) for row in db("""select v.*,p.position from youtube_playlist_items p join videos v on v.id=p.video
         where p.profile=? and p.playlist=? order by p.position""",(profile,ident))]
 downloads={row['id']:row for row in managed_downloads()}
 progress={row['id']:dict(row) for row in db('select id,current,duration,updated from playback_profile where profile=?',(profile,))}
 completed={row['id'] for row in db('select id from completed_playback where profile=?',(profile,))}
 local_files={embedded_youtube_id(video_metadata(path)['tags']):path for path,_,_ in local_catalogue()}
 for entry in entries:
  entry['thumbnail']=entry.pop('thumb','')
  local=local_files.get(entry['id'])
  local_id='local:'+str(local) if local else None
  if local:entry['thumbnail']='/api/thumb?video='+up.quote(str(local))
  elif entry['id'] in downloads:
   cover=downloads[entry['id']]['path'].with_suffix('.jpg')
   if cover.is_file():entry['thumbnail']='/api/thumb?path='+up.quote(str(cover))
  entry.update(available=bool(local or entry['id'] in downloads),kept=bool(local or downloads.get(entry['id'],{}).get('kept')),
               progress=progress.get(local_id) or progress.get(entry['id']),finished=entry['id'] in completed or local_id in completed)
 result['items']=entries
 result['filtered']=max(0,result['total']-len(entries)) if result['loaded_at'] else 0
 return result

def load_playlist(profile, ident, refresh=False):
 if not playlist_id(ident):return {'error':'Lien de playlist YouTube invalide.'}
 with playlist_lock:
  cached=playlist_entries(profile,ident)
  # Opening a saved collection must never wait for Internet access. Refresh is explicit.
  if cached and cached['loaded_at'] and not refresh:return cached
  try:
   token=google_access_token()
   entries=[]
   if token:
    response=gapi('playlists',{'part':'snippet,contentDetails','id':ident},token)
    if not response.get('items'):raise ValueError('Playlist inaccessible')
    summary=playlist_summary(response['items'][0]);page=''
    while True:
     response=gapi('playlistItems',{'part':'snippet,contentDetails','playlistId':ident,'maxResults':50,'pageToken':page},token)
     items=response.get('items',[])
     ids=list(dict.fromkeys(row.get('contentDetails',{}).get('videoId','') for row in items))
     ids=[i for i in ids if re.fullmatch(r'[A-Za-z0-9_-]{11}',i)]
     details=gapi('videos',{'part':'snippet,contentDetails,status','id':','.join(ids)},token) if ids else {'items':[]}
     videos={row['id']:row for row in details.get('items',[])}
     for row in items:
      vid=row.get('contentDetails',{}).get('videoId');video=videos.get(vid)
      if not video:continue
      sn=video['snippet'];duration=iso_duration(video.get('contentDetails',{}).get('duration',''))
      title=sn.get('title','Vidéo')
      if duration<=180 or re.search(r'#shorts|\bshorts\b',title,re.I):continue
      thumbs=sn.get('thumbnails',{})
      entries.append({'id':vid,'title':title,'duration':duration,'channel':sn.get('channelTitle','YouTube'),
                      'thumbnail':thumbs.get('high',thumbs.get('medium',{})).get('url',f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'),
                      'position':row.get('snippet',{}).get('position',len(entries))})
     page=response.get('nextPageToken','')
     if not page:break
   else:
    raw=subprocess.check_output(['yt-dlp','--no-warnings','--flat-playlist','--extractor-args','youtube:lang=fr','--dump-single-json',
                                 '--','https://www.youtube.com/playlist?list='+ident],stderr=subprocess.DEVNULL,text=True,timeout=90)
    info=json.loads(raw);raw_entries=info.get('entries') or []
    summary={'id':ident,'title':info.get('title','Playlist'),'description':info.get('description',''),
             'channel':info.get('channel') or info.get('uploader','YouTube'),'total':len(raw_entries),'thumbnail':''}
    for position,entry in enumerate(raw_entries):
     if not entry:continue
     vid=entry.get('id','');title=entry.get('title','Vidéo');duration=entry.get('duration') or 0
     if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid) or duration<=180 or re.search(r'#shorts|\bshorts\b',title,re.I):continue
     entries.append({'id':vid,'title':title,'duration':duration,'channel':entry.get('channel') or entry.get('uploader','YouTube'),
                     'thumbnail':f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg','position':position})
    summary['thumbnail']=entries[0]['thumbnail'] if entries else ''
   # Commit the complete ordered snapshot together. Offline use never sees half a refresh.
   save_playlist(profile,summary);add(entries)
   with sqlite3.connect(DB) as connection:
    connection.execute('delete from youtube_playlist_items where profile=? and playlist=?',(profile,ident))
    connection.executemany('insert into youtube_playlist_items values(?,?,?,?)',[(profile,ident,e['position'],e['id']) for e in entries])
    connection.execute('update youtube_playlists set loaded_at=? where profile=? and id=?',(time.time(),profile,ident))
   return playlist_entries(profile,ident)
  except (OSError,ValueError,subprocess.SubprocessError):
   if cached and cached['loaded_at']:
    cached['notice']='YouTube est indisponible. Voici la playlist enregistrée sur le Mac.'
    return cached
   return {'error':'Playlist inaccessible. Vérifie son lien et ta connexion Google si elle est privée.'}

SUBSCRIPTION_CHANNEL_LIMIT=100
SUBSCRIPTION_TTL=20*60
subscription_lock=threading.Lock()
subscription_cache=None

def subscription_playlists(token):
 channels=[]
 page=None
 while len(channels)<SUBSCRIPTION_CHANNEL_LIMIT:
  params={'part':'snippet','mine':'true','maxResults':50,'order':'relevance'}
  if page:params['pageToken']=page
  response=gapi('subscriptions',params,token)
  for item in response.get('items',[]):
   channel_id=item.get('snippet',{}).get('resourceId',{}).get('channelId')
   if channel_id and channel_id not in channels:channels.append(channel_id)
  next_page=response.get('nextPageToken')
  if not next_page or next_page==page:break
  page=next_page

 playlists={}
 for offset in range(0,min(len(channels),SUBSCRIPTION_CHANNEL_LIMIT),50):
  ids=channels[offset:min(offset+50,SUBSCRIPTION_CHANNEL_LIMIT)]
  response=gapi('channels',{'part':'contentDetails,snippet','id':','.join(ids)},token)
  for channel in response.get('items',[]):
   playlist=channel.get('contentDetails',{}).get('relatedPlaylists',{}).get('uploads')
   if playlist:
    playlists[playlist]=channel.get('snippet',{}).get('title','Abonnement')
 return playlists

def subscription_uploads(playlist, channel, token):
 params={'part':'snippet,contentDetails','playlistId':playlist,'maxResults':10}
 items=[]
 for video in gapi('playlistItems',params,token).get('items',[]):
  snippet=video.get('snippet',{})
  content=video.get('contentDetails',{})
  video_id=content.get('videoId') or snippet.get('resourceId',{}).get('videoId')
  title=snippet.get('title','')
  if not video_id or not title or title in ('Private video','Deleted video') or re.search(r'#shorts|\bshorts\b',title,re.I):
   continue
  thumbnails=snippet.get('thumbnails',{})
  cover=thumbnails.get('high',thumbnails.get('medium',{}))
  items.append({
   'id':video_id,'title':title,'thumbnail':cover.get('url',''),'channel':channel,
   'published_at':content.get('videoPublishedAt') or snippet.get('publishedAt',''),
  })
 return items

def subscription_suggestions(token):
 global subscription_cache
 fingerprint=hashlib.sha256(token.encode()).digest()
 with subscription_lock:
  if subscription_cache and subscription_cache[0]==fingerprint and subscription_cache[1]>time.monotonic():
   return subscription_cache[2]
  playlists=subscription_playlists(token)
  uploads=[]
  failures=[]
  with ThreadPoolExecutor(max_workers=10) as workers:
   futures=[workers.submit(subscription_uploads,playlist,channel,token) for playlist,channel in playlists.items()]
   for future in as_completed(futures):
    try:uploads.extend(future.result())
    except (OSError,ValueError) as error:failures.append(error)
  if playlists and len(failures)==len(playlists):raise failures[0]
  uploads.sort(key=lambda video:(video['published_at'],video['id']),reverse=True)
  unique={}
  for video in uploads:unique.setdefault(video['id'],video)
  ids=list(unique)
  suggestions=[]
  for offset in range(0,len(ids),50):
   batch=ids[offset:offset+50]
   response=gapi('videos',{'part':'contentDetails','id':','.join(batch)},token)
   durations={row['id']:iso_duration(row.get('contentDetails',{}).get('duration')) for row in response.get('items',[])}
   suggestions.extend(dict(unique[video_id],duration=durations[video_id]) for video_id in batch if durations.get(video_id,0)>180)
   if len(suggestions)>=48:break
  result={'connected':True,'items':suggestions[:48]}
  if failures:result['notice']='Certaines chaînes n’ont pas pu être actualisées.'
  if suggestions:add(suggestions[:48])
  subscription_cache=(fingerprint,time.monotonic()+(120 if failures else SUBSCRIPTION_TTL),result)
  return result

DOWNLOAD_SCRIPT=ROOT/'ytmp4.sh'
if not DOWNLOAD_SCRIPT.is_file():
 DOWNLOAD_SCRIPT=ROOT.parent/'ytmp4.sh'
DOWNLOAD_TIMEOUT=1800
active_downloads={}
download_groups={}
playback_downloads={}
download_lock=threading.Lock()
closing=threading.Event()

def signal_download_group(group, signum):
 try:
  os.killpg(group,signum)
 except ProcessLookupError:
  pass

def stop_downloads():
 for signum,delay in ((signal.SIGTERM,2),(signal.SIGKILL,2)):
  with download_lock:
   for group in download_groups:
    signal_download_group(group,signum)
  limit=time.monotonic()+delay
  while time.monotonic()<limit:
   with download_lock:
    if not active_downloads and not download_groups:return
   time.sleep(.05)

MIN_DOWNLOAD_FREE_BYTES=5*1024**3

def regular_file_size(path):
 try:
  info=path.stat(follow_symlinks=False)
  return info.st_size if stat.S_ISREG(info.st_mode) else 0
 except OSError:
  return 0

def directory_size(path):
 if path.is_symlink():return 0
 return sum(regular_file_size(pathlib.Path(root)/name)
            for root,_,files in os.walk(path,followlinks=False)
            for name in files)

def unused_cover_cache(remove=False, catalogue=None):
 if catalogue is None:catalogue=local_catalogue()
 paths={str(path) for path,_,_ in catalogue}
 paths.update(row['path'] for row in db('select path from imported_videos'))
 current={hashlib.sha1(path.encode()).hexdigest()+'.jpg' for path in paths}
 cache=DATA/'jaquettes'
 try:
  directory=os.open(cache,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 except OSError as error:
  if error.errno in (errno.ENOENT,errno.ENOTDIR,errno.ELOOP):return {'count':0,'bytes':0}
  raise
 try:
  older_than=time.time()-600
  count=size=0
  for name in os.listdir(directory):
   # Older builds named generated covers with Python's decimal hash.
   if name in current or not re.fullmatch(r'(?:[a-f0-9]{40}|[0-9]{1,19})\.jpg',name):continue
   try:
    entry=os.stat(name,dir_fd=directory,follow_symlinks=False)
    if not stat.S_ISREG(entry.st_mode) or entry.st_mtime>older_than:continue
    if remove:os.unlink(name,dir_fd=directory)
   except FileNotFoundError:
    continue
   count+=1
   size+=entry.st_size
  return {'count':count,'bytes':size}
 finally:
  os.close(directory)

def storage_usage():
 volume=shutil.disk_usage(DATA)
 catalogue=local_catalogue()
 temp=DATA/'À regarder'
 covers=DATA/'jaquettes'
 database_bytes=sum(regular_file_size(DB.with_name(DB.name+suffix))
                    for suffix in ('','-wal','-shm','-journal'))
 other_cache_bytes=directory_size(DATA/'cache')
 retained_bytes=sum(regular_file_size(entry['path']) for entry in managed_downloads() if entry['kept'])
 return {
  'available_bytes':volume.free,
  'volume_bytes':volume.total,
  'personal_video_bytes':sum(regular_file_size(path) for path,_,_ in catalogue),
  'temporary_bytes':max(0,directory_size(temp)-retained_bytes),
  'retained_video_bytes':retained_bytes,
  'cover_bytes':directory_size(covers),
  'unused_covers':unused_cover_cache(catalogue=catalogue),
  'other_cache_bytes':other_cache_bytes,
  'database_bytes':database_bytes,
 }

def playback_source(path, temporary=False):
 return {
  'ok':True, 'path':str(path), 'temporary':temporary, 'local':not temporary,
  'media_url':'/api/media?path='+urllib.parse.quote(str(path)),
 }

def managed_downloads(video_id=None):
 suffix=' where id=?' if video_id is not None else ''
 rows=db('select id,path,kept,created from temporary_downloads'+suffix,
         (video_id,) if video_id is not None else ())
 root=(DATA/'À regarder').resolve()
 for row in rows:
  path=pathlib.Path(row['path']).resolve()
  if root in path.parents and path.is_file():
   yield dict(row,path=path)

def download_status(video_id):
 with download_lock:
  if video_id not in active_downloads:
   job=playback_downloads.get(video_id)
   if job and job['event'].is_set() and job['result'] and not job['result'].get('ok'):
    return {'active':False,'error':job['result'].get('error','La préparation a échoué.')}
   return {'active':False}
  stage=active_downloads[video_id]
 pending={'active':True,'phase':'preparing'}
 if stage is None:return pending
 try:
  stream=json.loads((stage/'.cinema-stream.json').read_text())
  return {'active':True,'phase':'processing' if stream['complete'] else 'downloading',
          'percent':min(100,100*stream['seconds']/stream['duration'])}
 except (OSError,ValueError,KeyError,ZeroDivisionError):pass
 try:
  plan=json.loads((stage/'.cinema-formats.json').read_text(encoding='utf-8'))
  with (stage/'logs'/'latest.log').open('rb') as log:
   log.seek(max(0,os.fstat(log.fileno()).st_size-8192))
   lines=log.read(8192).decode('utf-8',errors='replace').splitlines()
 except (OSError,ValueError):
  return pending
 formats=[name for name,_ in plan['formats']]
 weights=[size for _,size in plan['formats']]
 if not all(weights):weights=[1]*len(formats)
 for line in reversed(lines):
  if not line.startswith('CINEMA_MEDIA '):continue
  try:
   name,percent,status=line.removeprefix('CINEMA_MEDIA ').rsplit(' ',2)
   name=json.loads(name)
  except ValueError:
   continue
  if name not in formats and name!=plan['combined']:continue
  if status=='finished' and name in (formats[-1],plan['combined']):
   return {'active':True,'phase':'processing','percent':100}
  try:
   number=float(percent)
  except ValueError:
   return {'active':True,'phase':'downloading'}
  if not math.isfinite(number):return {'active':True,'phase':'downloading'}
  number=max(0,min(100,number))
  if name in formats:
   index=formats.index(name)
   number=(100*sum(weights[:index])+number*weights[index])/sum(weights)
  return {'active':True,'phase':'downloading','percent':min(99.9,number)}
 return pending

JOB_MARKER='.cinema-download'
ABANDONED_JOB_AGE=24*60*60

def mark_job(stage, group=None):
 marker=stage/JOB_MARKER
 marker.write_text(json.dumps({'server':os.getpid(),'group':group}),encoding='utf-8')

def job_process_exists(pid, group=False):
 if not isinstance(pid,int) or not 1<pid<2**31:return False
 try:
  (os.killpg if group else os.kill)(pid,0)
  return True
 except ProcessLookupError:
  return False
 except PermissionError:
  return True

def cleanup_abandoned_downloads():
 root=DATA.resolve()/'À regarder'
 if root.is_symlink() or not root.is_dir():return
 cutoff=time.time()-ABANDONED_JOB_AGE
 try:
  registered={pathlib.Path(row['path']).resolve() for row in db('select path from temporary_downloads')}
  folders=list(root.iterdir())
 except (OSError,RuntimeError,sqlite3.Error):
  return
 for folder in folders:
  if closing.is_set():return
  if not re.fullmatch(r'[0-9a-f]{16}',folder.name) or folder.is_symlink() or not folder.is_dir():
   continue
  if any(folder in path.parents for path in registered):continue
  marker=folder/JOB_MARKER
  try:
   info=marker.stat(follow_symlinks=False)
   if not stat.S_ISREG(info.st_mode) or info.st_mtime>=cutoff or info.st_size>1024:continue
   owner=json.loads(marker.read_text(encoding='utf-8'))
   if not isinstance(owner,dict) or not isinstance(owner.get('server'),int):continue
   if job_process_exists(owner.get('server')) or job_process_exists(owner.get('group'),group=True):continue
   shutil.rmtree(folder)
  except (OSError,ValueError):
   continue

def download_interrupted(cancel=None):
 return closing.is_set() or (cancel is not None and cancel.is_set())

def download_attempt(stage, command, environment, cancel, timeout):
 with download_lock:
  if download_interrupted(cancel):return None
  process=subprocess.Popen(command,env=environment,start_new_session=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,errors='replace')
  download_groups[process.pid]=cancel
 try:
  with process:
   try:
    mark_job(stage,process.pid)
    _,stderr=process.communicate(timeout=timeout)
   except (OSError,subprocess.TimeoutExpired):
    signal_download_group(process.pid,signal.SIGKILL)
    process.communicate()
    raise
   return process.returncode,stderr
 finally:
  with download_lock:download_groups.pop(process.pid,None)

YOUTUBE_DOWNLOAD_LIMITS={
 'is_live':'Ce direct est disponible sur YouTube. Attends le replay pour le télécharger.',
 'is_upcoming':'Cette diffusion n’a pas encore commencé.',
 'post_live':'YouTube prépare encore le replay. Réessaie dans quelques minutes.',
 'unavailable':'YouTube ne propose pas de fichier que Fauteuil puisse télécharger pour le moment.',
}
YOUTUBE_STATE_TTL=5*60

def youtube_video_state(data):
 live_status=data.get('live_status')
 if live_status in ('is_live','is_upcoming','post_live'):return live_status
 if data.get('availability') in ('private','premium_only','subscriber_only','needs_auth') or data.get('formats')==[]:
  return 'unavailable'
 return 'recorded'

def prepare_download(video_id, keep=False, cancel=None, progressive=False):
 temp_root=DATA.resolve()/'À regarder'
 if temp_root.is_symlink():
  return {'ok':False,'error':'Le dossier de téléchargement de Fauteuil doit être un dossier local.'}
 cached=next(managed_downloads(video_id),None)
 if cached:
  if not keep or cached['kept']:
   return dict(playback_source(cached['path'],temporary=not cached['kept']),kept=bool(cached['kept']),local=False)
  with sqlite3.connect(DB) as connection:
   pinned=connection.execute('update temporary_downloads set kept=1,created=? where id=? and path=?',
                             (time.time(),video_id,str(cached['path']))).rowcount
  if pinned:return dict(playback_source(cached['path']),kept=True,local=False)
 state=db('select youtube_state from video_synopses where id=? and fetched_at>?',(video_id,time.time()-YOUTUBE_STATE_TTL))
 if state and state[0]['youtube_state'] in YOUTUBE_DOWNLOAD_LIMITS:
  return {'ok':False,'error':YOUTUBE_DOWNLOAD_LIMITS[state[0]['youtube_state']]}
 if not DOWNLOAD_SCRIPT.is_file():
  return {'ok':False,'error':'Le téléchargement est momentanément indisponible. Réessaie plus tard.'}
 if shutil.disk_usage(DATA).free<MIN_DOWNLOAD_FREE_BYTES:
  return {'ok':False,'error':'Espace disque insuffisant. Fauteuil garde au moins 5 Gio libres sur le Mac.'}

 stage=temp_root/secrets.token_hex(8)
 stage.mkdir(parents=True)
 with download_lock:
  if video_id in active_downloads:active_downloads[video_id]=stage
  if progressive and video_id in playback_downloads:playback_downloads[video_id]['stage']=stage
 keep_stage=False
 try:
  mark_job(stage)
  environment=dict(os.environ,YT_MOVIES_DIR=str(stage),YT_CINEMA_PROGRESS='1',
                   YT_CINEMA_RESERVE_BYTES=str(MIN_DOWNLOAD_FREE_BYTES))
  if progressive:environment['YT_CINEMA_STREAM']='1'
  command=[str(DOWNLOAD_SCRIPT),'--no-archive','--fr-subs','https://www.youtube.com/watch?v='+video_id]
  deadline=time.monotonic()+DOWNLOAD_TIMEOUT
  attempts=0
  while attempts<2:
   attempts+=1
   remaining=deadline-time.monotonic()
   if remaining<=0:
    return {'ok':False,'error':'Le téléchargement a dépassé le délai autorisé. Tu peux réessayer.'}
   try:
    result=download_attempt(stage,command,environment,cancel,remaining)
   except subprocess.TimeoutExpired:
    return {'ok':False,'error':'Le téléchargement a dépassé le délai autorisé. Tu peux réessayer.'}
   if result is None or download_interrupted(cancel):
    return {'ok':False,'error':'Téléchargement interrompu.'}
   return_code,stderr=result
   if return_code==0:break
   if progressive and not re.search(r'CINEMA_DISK_REQUIRED|no space left on device|errno 28',stderr,re.I):
    # The standard downloader handles formats and endpoints FFmpeg cannot stream.
    progressive=False
    environment.pop('YT_CINEMA_STREAM',None)
    shutil.rmtree(stage/'stream',ignore_errors=True)
    (stage/'.cinema-stream.json').unlink(missing_ok=True)
    continue
   if 'HTTP Error 403' not in stderr:break
  if return_code:
   disk_estimate=re.search(r'CINEMA_DISK_REQUIRED (\d+) (\d+)',stderr)
   if disk_estimate:
    unit=1024**3
    required,available=(float(value)/unit for value in disk_estimate.groups())
    message=(f'Espace insuffisant avant le téléchargement. Prévois environ {required:.2f} Gio '
             f'pour le fichier et son assemblage. Il reste {available:.2f} Gio.')
    return {'ok':False,'error':message}
   if 'no space left on device' in stderr.lower() or 'errno 28' in stderr.lower():
    return {'ok':False,'error':'Le téléchargement s’est arrêté faute d’espace disque disponible.'}
   if 'HTTP Error 403' in stderr:
    return {'ok':False,'error':'YouTube a encore refusé le téléchargement après une nouvelle tentative (erreur 403).'}
   return {'ok':False,'error':'Le téléchargement a échoué. Vérifie ta connexion puis réessaie.'}
  candidates=[p for p in (stage/'Movies').rglob('*.mp4') if p.is_file() and p.stat().st_size>0]
  if not candidates:
   try:
    log=(stage/'logs'/'latest.log').read_text(encoding='utf-8',errors='replace')
   except OSError:
    log=''
   if 'does not pass filter (live_status' in log:
    return {'ok':False,'error':'Attends que le replay soit disponible sur YouTube avant de télécharger cette vidéo.'}
   return {'ok':False,'error':'Le téléchargement n’a produit aucun fichier MP4.'}
  path=max(candidates,key=lambda p:p.stat().st_mtime).resolve()
  db('insert or replace into temporary_downloads(id,path,created,kept) values(?,?,?,?)',
     (video_id,str(path),time.time(),int(keep)),write=True)
  keep_stage=True
  shutil.rmtree(stage/'logs',ignore_errors=True)
  try:(stage/'.cinema-formats.json').unlink(missing_ok=True)
  except OSError:pass
  return dict(playback_source(path,temporary=not keep),kept=keep,local=False)
 finally:
  if not keep_stage:shutil.rmtree(stage,ignore_errors=True)

TRASH_COMMAND='/usr/bin/trash'

def remove_managed_download(video_id):
 with download_lock:
  if video_id in active_downloads:
   return {'ok':False,'error':'Cette vidéo est en cours de préparation.'}
  active_downloads[video_id]=None
 try:
  with sqlite3.connect(DB) as connection:
   connection.execute('begin immediate')
   row=connection.execute('select path from temporary_downloads where id=?',(video_id,)).fetchone()
   if row is None:
    return {'ok':False,'error':'Aucun téléchargement pour cette vidéo.'}
   root=DATA.resolve()/'À regarder'
   path=pathlib.Path(row[0]).resolve()
   if root.is_symlink() or root not in path.parents:
    return {'ok':False,'error':'Emplacement de téléchargement non reconnu.'}
   parts=path.relative_to(root).parts
   if len(parts)<3 or parts[1]!='Movies' or not re.fullmatch(r'[0-9a-f]{16}',parts[0]):
    return {'ok':False,'error':'Ce téléchargement provient d’une ancienne version de Fauteuil.'}
   folder=root/parts[0]
   others=connection.execute('select path from temporary_downloads where id<>?',(video_id,)).fetchall()
   if any(folder in pathlib.Path(other[0]).resolve().parents for other in others):
    return {'ok':False,'error':'Ce dossier contient aussi un autre téléchargement.'}
   if folder.exists():
    if not shutil.which(TRASH_COMMAND):
     return {'ok':False,'error':'La commande de corbeille macOS est indisponible sur ce Mac.'}
    try:
     subprocess.run([TRASH_COMMAND,'-s',str(folder)],capture_output=True,check=True,timeout=4)
    except (OSError,subprocess.SubprocessError):
     return {'ok':False,'error':'macOS n’a pas pu déplacer ce téléchargement vers la Corbeille.'}
   connection.execute('delete from temporary_downloads where id=?',(video_id,))
  return {'ok':True}
 except (OSError,sqlite3.Error):
  return {'ok':False,'error':'Le téléchargement n’a pas pu être retiré.'}
 finally:
  with download_lock:active_downloads.pop(video_id,None)

def retry_playback_cleanup(video_id=None):
 # Keep the request and the catalogue row until the filesystem operation succeeds.
 with download_lock:
  root=DATA.resolve()/'À regarder'
  if root.is_symlink():return
  condition=' where id=?' if video_id is not None else ''
  args=(video_id,) if video_id is not None else ()
  try:
   with sqlite3.connect(DB) as connection:
    connection.execute('begin immediate')
    for ident,filename in connection.execute('select id,path from pending_playback_cleanup'+condition,args).fetchall():
     if ident in active_downloads:continue
     row=connection.execute('select kept from temporary_downloads where id=? and path=?',(ident,filename)).fetchone()
     path=pathlib.Path(filename)
     if row and not row[0] and root in path.parents and path.resolve()==path:
      relative=path.relative_to(root).parts
      try:
       others=connection.execute('select path from temporary_downloads where id<>? or path<>?',(ident,filename))
       folder=root/relative[0]
       other_paths=[pathlib.Path(other[0]).resolve() for other in others]
       if path in other_paths:
        continue
       if (len(relative)>=3 and relative[1]=='Movies' and re.fullmatch(r'[0-9a-f]{16}',relative[0])
           and not any(folder in other.parents for other in other_paths)):
        if folder.exists():shutil.rmtree(folder)
       else:path.unlink(missing_ok=True)
      except OSError:
       continue
      connection.execute('delete from temporary_downloads where id=? and path=? and kept=0',(ident,filename))
     connection.execute('delete from pending_playback_cleanup where id=? and path=?',(ident,filename))
  except sqlite3.Error:
   # The durable request can be retried at the next launch.
   return

def recover_finished_downloads():
 # Repair older copies when the history proves they were watched after this download.
 db("""insert or ignore into pending_playback_cleanup
       select d.id,d.path from temporary_downloads d
       where d.kept=0 and exists (select 1 from completed_playback h
                                  where h.id=d.id and h.finished_at>=d.created)
       and not exists (select 1 from playback_profile p where p.id=d.id and p.updated>=d.created)""",write=True)
 retry_playback_cleanup()

def run_download(video_id, keep=False, cancel=None, progressive=False):
 if download_interrupted(cancel):return {'ok':False,'error':'Téléchargement interrompu.'}
 local=local_youtube_file(video_id)
 if local is not None:
  return dict(playback_source(local),id='local:'+str(local))
 with download_lock:
  if video_id in active_downloads:
   return {'ok':False,'error':'Cette vidéo est déjà en cours de téléchargement.'}
  active_downloads[video_id]=None
 try:
  return prepare_download(video_id,keep=keep,cancel=cancel,progressive=progressive)
 except OSError as error:
  message=('L’espace disque est insuffisant.' if error.errno==errno.ENOSPC else
           'Fauteuil n’a pas pu enregistrer le fichier.')
  return {'ok':False,'error':message}
 except sqlite3.Error:
  return {'ok':False,'error':'Fauteuil n’a pas pu enregistrer le téléchargement dans sa base.'}
 finally:
  with download_lock:
   active_downloads.pop(video_id,None)
  retry_playback_cleanup(video_id)

def progressive_source(video_id, start=0, wait_for_file=False):
 # Repeated clicks join the same job. The request returns after two complete segments.
 local=local_youtube_file(video_id)
 if local is not None:return dict(playback_source(local),id='local:'+str(local))
 cached=next(managed_downloads(video_id),None)
 if cached:return dict(playback_source(cached['path'],temporary=not cached['kept']),local=False)
 with download_lock:
  job=playback_downloads.get(video_id)
  if job is None or job['event'].is_set():
   if video_id in active_downloads:
    return {'ok':False,'error':'Cette vidéo est déjà en préparation pour le hors ligne. Réessaie à la fin du téléchargement.'}
   for old_id,old in list(playback_downloads.items()):
    if old['event'].is_set() and (old['detached'] or not old['result'].get('ok')):playback_downloads.pop(old_id,None)
   job={'event':threading.Event(),'result':None,'detached':False,'stage':None}
   playback_downloads[video_id]=job
   def worker():
    try:
     job['result']=run_download(video_id,progressive=True)
    except Exception:
     job['result']={'ok':False,'error':'La préparation de la vidéo a échoué. Réessaie.'}
    finally:
     try:
      stage=job['stage']
      if stage and job['detached']:
       shutil.rmtree(stage/'stream',ignore_errors=True)
     finally:job['event'].set()
   threading.Thread(target=worker,daemon=True).start()
  job['detached']=False
 try:start=max(0,float(start))
 except (TypeError,ValueError):start=0
 if not math.isfinite(start):start=0
 while not closing.is_set():
  if job['event'].wait(.2):return job['result']
  if wait_for_file:continue
  with download_lock:stage=active_downloads.get(video_id)
  if stage is None:continue
  job['stage']=stage
  try:
   state=json.loads((stage/'.cinema-stream.json').read_text())
   manifest=(stage/'stream/index.m3u8').read_text()
   buffered=sum(float(n) for n in re.findall(r'#EXTINF:([0-9.]+)',manifest))
   if buffered < min(state['duration'],start+8):continue
   return {'ok':True,'path':state['path'],'temporary':True,'local':False,'progressive':True,
           'duration':state['duration'],'media_url':f'/api/stream/{stage.name}/index.m3u8'}
  except (OSError,ValueError,KeyError):continue
 return {'ok':False,'error':'La préparation a été interrompue.'}

trip_lock=threading.Lock()
trip={'profile':None,'active':False,'items':[]}
TRIP_LIMIT=20

def prepare_trip(profile, selection, allowed=None):
 if not isinstance(selection,list) or not 1<=len(selection)<=TRIP_LIMIT:
  return {'ok':False,'error':'Sélectionne entre une et vingt vidéos.'}
 if any(not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id) for video_id in selection):
  return {'ok':False,'error':'La sélection contient un titre non téléchargeable.'}
 saved=allowed if allowed is not None else {row['id']:row['title'] for row in db('select id,title from watchlist_profile where profile=?',(profile,))}
 if any(video_id not in saved for video_id in selection):
  return {'ok':False,'error':'Ajoute ces vidéos à Ma liste avant de les sélectionner.'}
 entries=[{'id':video_id,'title':saved[video_id],'state':'pending'} for video_id in dict.fromkeys(selection)]
 with trip_lock:
  if trip['active']:
   return {'ok':False,'error':'Une sélection est déjà en cours de préparation.'}
  cancel=threading.Event()
  trip.update(profile=profile,active=True,items=entries,cancel=cancel)

 def worker():
  try:
   for entry in entries:
    with trip_lock:
     if download_interrupted(cancel):break
     entry['state']='downloading'
    try:
     result=run_download(entry['id'],keep=True,cancel=cancel)
    except (OSError,sqlite3.Error,ValueError):
     result={'ok':False,'error':'Impossible de préparer cette vidéo.'}
    with trip_lock:
     if result.get('ok'):
      entry['state']='ready'
     elif cancel.is_set():
      entry['state']='cancelled'
     else:
      entry.update(state='failed',error=result.get('error','Téléchargement échoué.'))
  finally:
   with trip_lock:
    for entry in entries:
     if entry['state'] in ('pending','downloading'):entry['state']='cancelled'
    trip['active']=False
 threading.Thread(target=worker,daemon=True).start()
 return {'ok':True}

def cancel_trip(profile):
 with trip_lock:
  if trip['profile']!=profile or not trip['active']:
   return {'ok':False,'error':'Aucune préparation en cours pour ce profil.'}
  cancel=trip['cancel']
  if cancel.is_set():return {'ok':True}
  cancel.set()

 def signal_trip(signum):
  with download_lock:
   for group,owner in download_groups.items():
    if owner is cancel:signal_download_group(group,signum)

 signal_trip(signal.SIGTERM)
 force=threading.Timer(2,signal_trip,args=(signal.SIGKILL,))
 force.daemon=True
 force.start()
 return {'ok':True}

def trip_status(profile):
 with trip_lock:
  if trip['profile']!=profile:return {'active':False,'items':[]}
  status={'active':trip['active'],'cancelling':trip['active'] and trip['cancel'].is_set(),
          'items':[dict(entry) for entry in trip['items']]}
 for entry in status['items']:
  if entry['state']=='downloading':entry['progress']=download_status(entry['id'])
 return status

class H(http.server.SimpleHTTPRequestHandler):
 def parse_request(self):
  if not super().parse_request():return False
  port=self.server.server_port
  if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
   self.send_error(403,'Serveur local')
   return False
  origin=self.headers.get('Origin')
  if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
   self.send_error(403,'Origine externe interdite')
   return False
  url=up.urlparse(self.path)
  callback=self.command=='GET' and url.path=='/' and 'state' in up.parse_qs(url.query)
  if self.headers.get('Sec-Fetch-Site')=='cross-site' and not callback:
   self.send_error(403,'Requête externe interdite')
   return False
  return True

 def log_message(self,*a):pass
 def sendj(self,x):
  b=json.dumps(x).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def send_media(self, query):
  path=pathlib.Path(urllib.parse.parse_qs(query).get('path',[''])[0]).resolve()
  temporary_root=(DATA/'À regarder').resolve()
  content_type=VIDEO_CONTENT_TYPES.get(path.suffix.lower())
  if not content_type or not path.is_file() or (temporary_root not in path.parents and not is_local_video(path)):
   self.send_error(404)
   return
  try:
   stream=path.open('rb')
  except OSError:
   self.send_error(404)
   return
  with stream:
   size=os.fstat(stream.fileno()).st_size
   requested_range=self.headers.get('Range') if self.command=='GET' else None
   start,end=0,size-1
   if requested_range:
    match=re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})',requested_range)
    if match and match[1]:
     start=int(match[1])
     end=min(end,int(match[2])) if match[2] else end
    elif match and match[2]:
     start=max(0,size-int(match[2]))
    else:
     start=size
    if start>end:
     self.send_response(416)
     self.send_header('Content-Range',f'bytes */{size}')
     self.send_header('Accept-Ranges','bytes')
     self.send_header('Content-Length','0')
     self.end_headers()
     return
   self.send_response(206 if requested_range else 200)
   if requested_range:
    self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
   self.send_header('Accept-Ranges','bytes')
   self.send_header('Content-Type',content_type)
   self.send_header('Cache-Control','no-store')
   self.send_header('Content-Length',str(end-start+1))
   self.end_headers()
   if self.command=='HEAD':
    return
   stream.seek(start)
   remaining=end-start+1
   while remaining:
    chunk=stream.read(min(1024*1024,remaining))
    if not chunk:break
    try:
     self.wfile.write(chunk)
    except (BrokenPipeError,ConnectionResetError):
     break
    remaining-=len(chunk)

 def send_stream(self, route):
  match=re.fullmatch(r'/api/stream/([0-9a-f]{16})/(index\.m3u8|init\.mp4|segment[0-9]{6}\.m4s)',route)
  if not match:return self.send_error(404)
  stage=(DATA/'À regarder'/match[1]).resolve()
  if stage.parent!=(DATA/'À regarder').resolve():return self.send_error(404)
  with download_lock:active=stage in active_downloads.values()
  registered=any(pathlib.Path(row['path']).resolve().is_relative_to(stage) for row in db('select path from temporary_downloads'))
  if not active and not registered:return self.send_error(404)
  folder=stage/'stream'
  path=folder/match[2]
  if folder.is_symlink() or path.is_symlink() or not path.is_file():return self.send_error(404)
  body=path.read_bytes()
  self.send_response(200)
  self.send_header('Content-Type','application/vnd.apple.mpegurl' if path.suffix=='.m3u8' else 'video/mp4')
  self.send_header('Cache-Control','no-store')
  self.send_header('Content-Length',str(len(body)))
  self.end_headers()
  if self.command!='HEAD':
   try:self.wfile.write(body)
   except (BrokenPipeError,ConnectionResetError):pass

 def do_HEAD(self):
  url=urllib.parse.urlparse(self.path)
  if url.path.startswith('/api/stream/'):return self.send_stream(url.path)
  if url.path=='/api/media':
   return self.send_media(url.query)
  return super().do_HEAD()

 def google_callback(self, query):
  state=query.get('state',[''])[0]
  with oauth_lock:
   attempt=oauth_attempt.get(state)
   if not attempt or attempt['status']!='pending' or time.monotonic()>attempt['expires']:
    return self.send_error(400,'Cette demande de connexion Google est invalide ou expirée.')
   attempt['status']='exchanging'

  code=query.get('code',[''])[0]
  token=None
  message='Google a refusé l’autorisation. Tu peux réessayer depuis Fauteuil.'
  if code and not query.get('error'):
   form={
    'code':code, 'client_id':attempt['client_id'], 'client_secret':attempt['secret'],
    'code_verifier':attempt['verifier'], 'redirect_uri':OAUTH_REDIRECT, 'grant_type':'authorization_code',
   }
   try:
    request=urllib.request.Request('https://oauth2.googleapis.com/token',data=up.urlencode(form).encode(),
                                   headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request,timeout=25,context=google_ssl_context()) as response:
     token=json.load(response)
    if not token.get('access_token') or not token.get('refresh_token'):
     token=None
     message='Google n’a pas fourni les jetons nécessaires. Relance la connexion.'
   except (OSError,ValueError):
    message='Fauteuil n’a pas pu terminer la connexion avec Google. Relance la connexion.'

  with oauth_lock:
   if oauth_attempt.get(state) is not attempt:
    return self.send_error(400,'Cette demande de connexion a été remplacée.')
   if token:
    if keychain('set',token['refresh_token']):
     with sqlite3.connect(DB) as connection:
      connection.execute("delete from settings where key='google_reconnect'")
      connection.executemany('insert or replace into settings values(?,?)',[
       ('google_access',token['access_token']),
       ('google_expiry',str(time.time()+token.get('expires_in',3600))),
      ])
     attempt['status']='connected'
     message='Compte Google connecté. Tu peux revenir dans Fauteuil.'
    else:
     message='Le trousseau macOS n’a pas pu enregistrer la connexion Google.'
   if attempt['status']!='connected':attempt['status']='error'
   attempt['message']=message
   attempt.pop('secret',None)
   attempt.pop('verifier',None)
  body=('<!doctype html><meta charset="utf-8"><title>Fauteuil</title><p>'+message+'</p>').encode('utf-8')
  self.send_response(200)
  self.send_header('Content-Type','text/html; charset=utf-8')
  self.send_header('Content-Length',str(len(body)))
  self.send_header('Cache-Control','no-store')
  self.end_headers()
  self.wfile.write(body)

 def local_details(self, path, requested_id):
  profile=active_profile()
  local_id='local:'+str(path)
  saved=bool(db('select id from watchlist_profile where profile=? and id=?',(profile,requested_id)))
  progress=db('select current,duration,updated from playback_profile where profile=? and id=?',(profile,local_id))
  meta=video_metadata(path)
  tags=meta['tags']
  return {
   'id':requested_id,'title':display_title(path),'local':True,
   'description':tags.get('synopsis') or tags.get('description') or 'Vidéo locale de votre bibliothèque.',
   'channel':tags.get('artist') or path.parent.name,'duration':int(meta['duration']),'genre':genre_label(tags.get('genre')),
   'thumbnail':local_cover_url(path),
   'watchlisted':saved,'progress':dict(progress[0]) if progress else None,
  }

 def downloaded_details(self, entry):
  video_id=entry['id']
  path=entry['path']
  profile=active_profile()
  saved=bool(db('select 1 from watchlist_profile where profile=? and id=?',(profile,video_id)))
  progress=db('select current,duration,updated from playback_profile where profile=? and id=?',(profile,video_id))
  metadata=video_metadata(path)
  tags=metadata['tags']
  cover=path.with_suffix('.jpg')
  return {
   'id':video_id,'title':display_title(path),'downloaded':True,'kept':bool(entry['kept']),
   'description':tags.get('synopsis') or tags.get('description') or 'Vidéo disponible hors ligne.',
   'channel':tags.get('artist') or 'YouTube','duration':int(metadata['duration']),'genre':genre_label(tags.get('genre')),
   'thumbnail':'/api/thumb?path='+urllib.parse.quote(str(cover)) if cover.is_file() else '',
   'watchlisted':saved,'progress':dict(progress[0]) if progress else None,
  }

 def remote_details(self, video_id, preview=False):
  profile=active_profile()
  saved=db('select title,thumb,duration,channel from watchlist_profile where profile=? and id=?',(profile,video_id))
  progress=db('select current,duration,updated from playback_profile where profile=? and id=?',(profile,video_id))
  catalogued=db('select title,thumb,duration,channel,genre from videos where id=?',(video_id,)) or saved
  rows=db('select description,fetched_at,youtube_state from video_synopses where id=?',(video_id,))
  synopsis=dict(rows[0]) if rows else {}
  state=synopsis.get('youtube_state') or None
  ttl=7*86400 if state=='recorded' else YOUTUBE_STATE_TTL
  fresh=bool(state and time.time()-synopsis['fetched_at']<ttl)
  details=None
  notice=''
  if catalogued and (fresh or preview):
   details=dict(catalogued[0],description=synopsis.get('description',''),thumbnail=catalogued[0]['thumb'],
                youtube_state=state if fresh else None,metadata_pending=not fresh)
  else:
   try:
    command=['yt-dlp','--no-warnings','--extractor-args','youtube:lang=fr','--dump-single-json','--skip-download','--ignore-no-formats-error','--socket-timeout','5',
             '--extractor-retries','0','https://www.youtube.com/watch?v='+video_id]
    data=json.loads(subprocess.check_output(command,stderr=subprocess.DEVNULL,text=True,timeout=12))
    details={
     'id':video_id,'title':data.get('title') or 'Vidéo YouTube',
     'description':(data.get('description') or '')[:12000],
     'channel':data.get('channel') or data.get('uploader') or 'YouTube',
     'duration':data.get('duration') or 0,'thumbnail':data.get('thumbnail') or '',
     'genre':genre_label(next(iter(data.get('categories') or []),'')),
     'youtube_state':youtube_video_state(data),
    }
    add([details])
    with sqlite3.connect(DB) as connection:
     connection.execute('insert or replace into video_synopses(id,description,fetched_at,youtube_state) values(?,?,?,?)',
                        (video_id,details['description'],time.time(),details['youtube_state']))
     connection.execute('delete from video_synopses where id in (select id from video_synopses order by fetched_at desc limit -1 offset 500)')
   except (OSError,ValueError,subprocess.SubprocessError):
    if not catalogued:return {'error':'Impossible de récupérer la fiche sur YouTube.'}
    info=catalogued[0]
    details=dict(info,description=synopsis.get('description',''),thumbnail=info['thumb'])
    notice='YouTube est indisponible. Fauteuil affiche les informations enregistrées.'
  details.update(id=video_id,watchlisted=bool(saved),progress=dict(progress[0]) if progress else None,notice=notice)
  return details

 def native_request(self):
  secret=os.environ.get('CINEMA_IMPORT_TOKEN')
  received=self.headers.get('X-Cinema-Import','')
  return bool(secret and secrets.compare_digest(secret.encode(),received.encode()))

 def do_GET(self):
  u=urllib.parse.urlparse(self.path)
  if u.path=='/api/local/imports':
   entries=[{'path':str(path),'title':display_title(path),'available':available}
            for path,available in imported_entries()]
   return self.sendj(entries)
  if u.path=='/api/health':
   if not self.native_request():
    self.send_error(403)
    return
   return self.sendj({'ok':True})
  if u.path.startswith('/api/stream/'):return self.send_stream(u.path)
  if u.path=='/api/media':
   return self.send_media(u.query)
  if u.path=='/api/download/trip':
   return self.sendj(trip_status(active_profile()))
  if u.path=='/api/download/status':
   video_id=urllib.parse.parse_qs(u.query).get('id',[''])[0]
   return self.sendj(download_status(video_id))
  if u.path=='/api/playlists':
   return self.sendj({'items':[dict(row) for row in db('select id,title,description,thumbnail,channel,total,loaded_at from youtube_playlists where profile=? order by title collate nocase',(active_profile(),))]})
  if u.path=='/api/playlist':
   query=up.parse_qs(u.query)
   return self.sendj(load_playlist(active_profile(),query.get('id',[''])[0],refresh=query.get('refresh')==['1']))
  if u.path=='/api/storage':
   try:
    return self.sendj(storage_usage())
   except OSError:
    return self.sendj({'error':'Impossible de mesurer l’espace de stockage.'})
  if u.path=='/detail':
   self.path='/detail.html';return super().do_GET()
  if u.path=='/api/library':
   profile=active_profile()
   downloads={entry['id']:entry for entry in managed_downloads()}
   saved={r['id'] for r in db('select id from watchlist_profile where profile=?',(profile,))}
   progress={r['id']:dict(r) for r in db('select id,current,duration,updated from playback_profile where profile=?',(profile,))}
   finished={row['id']:row['finished_at'] for row in db('select id,finished_at from completed_playback where profile=?',(profile,))}
   items=[dict(x) for x in db('select * from videos order by title') if int(x['duration'] or 0)>180 and not re.search(r'#shorts|\bshorts\b',x['title'] or '',re.I)]
   for x in items:
    x['thumbnail']=x.get('thumb','');x['watchlisted']=x['id'] in saved;x['progress']=progress.get(x['id']);x['finished_at']=finished.get(x['id'])
   known={item['id'] for item in items}
   for video_id,entry in downloads.items():
    if video_id in known:continue
    metadata=video_metadata(entry['path'])
    items.append({
     'id':video_id,'title':display_title(entry['path']),'duration':int(metadata['duration']),
     'channel':metadata['tags'].get('artist') or 'YouTube','genre':genre_label(metadata['tags'].get('genre')),
     'watchlisted':video_id in saved,'progress':progress.get(video_id),'finished_at':finished.get(video_id),
    })
   for item in items:
    entry=downloads.get(item['id'])
    if entry:
     item['downloaded']=True
     item['kept']=bool(entry['kept'])
     if entry['kept']:item['added_at']=entry['created']
     cover=entry['path'].with_suffix('.jpg')
     if cover.is_file():item['thumbnail']='/api/thumb?path='+urllib.parse.quote(str(cover))
   imported={row['path']:row['added_at'] for row in db('select path,added_at from imported_videos')}
   for path,series_root,season in local_catalogue():
    try:
     file_stat=path.stat()
    except OSError:
     continue
    added_at=imported.get(str(path),getattr(file_stat,'st_birthtime',file_stat.st_mtime))
    metadata=video_metadata(path)
    video_id='local:'+str(path)
    items.append({
     'id':video_id,'title':display_title(path),'thumbnail':local_cover_url(path),
     'duration':int(metadata['duration']),'channel':metadata['tags'].get('artist') or 'Sur cet appareil',
     'genre':genre_label(metadata['tags'].get('genre')),
     'local':True,'path':str(path),'source_id':embedded_youtube_id(metadata['tags']),'added_at':added_at,
     'collection':series_root.name.replace('_',' ') if series_root else 'Films',
     'series_id':series_identifier(series_root) if series_root else None,
     'season':'Saison '+str(season) if season else '', 'season_number':season,
     'watchlisted':video_id in saved,'progress':progress.get(video_id),'finished_at':finished.get(video_id)
    })
   return self.sendj(items)
  if u.path=='/api/profiles':
   return self.sendj({'active':active_profile(),'profiles':[dict(x) for x in db('select id,name,avatar from profiles order by rowid')]})
  if u.path=='/api/watchlist':
   rows=[dict(x) for x in db('select id,title,thumb,duration,channel,added from watchlist_profile where profile=? order by added desc',(active_profile(),)) if int(x['duration'] or 0)>180 and not re.search(r'#shorts|\bshorts\b',x['title'] or '',re.I)]
   for x in rows:x['thumbnail']=x.pop('thumb','');x['watchlisted']=True
   return self.sendj(rows)
  if u.path=='/' and 'state' in urllib.parse.parse_qs(u.query):
   return self.google_callback(urllib.parse.parse_qs(u.query))
  if u.path=='/api/google/trending':
   token=google_access_token()
   if not token:return self.sendj({'items':[]})
   try:
    data=gapi('videos',{'part':'snippet,contentDetails','chart':'mostPopular','regionCode':'FR','maxResults':30},token);items=[]
    for v in data.get('items',[]):
     sn=v.get('snippet',{});title=sn.get('title','')
     duration=iso_duration(v.get('contentDetails',{}).get('duration'))
     if v.get('id') and duration>180 and not re.search(r'#shorts|\bshorts\b',title,re.I):items.append({'id':v['id'],'title':title,'thumbnail':sn.get('thumbnails',{}).get('high',sn.get('thumbnails',{}).get('medium',{})).get('url',''),'duration':duration,'channel':sn.get('channelTitle','YouTube'),'category':sn.get('categoryId')})
    add(items);return self.sendj({'items':items})
   except Exception as e:return self.sendj({'items':[],'error':str(e)})
  if u.path=='/api/google/suggestions':
   token=google_access_token()
   if not token:
    if db("select 1 from settings where key='google_reconnect'"):
     return self.sendj({'connected':False,'items':[],'notice':GOOGLE_RECONNECT_MESSAGE})
    if keychain('get'):
     return self.sendj({'connected':True,'items':[],'error':'Les abonnements YouTube sont momentanément indisponibles.'})
    return self.sendj({'connected':False,'items':[]})
   try:
    return self.sendj(subscription_suggestions(token))
   except (OSError,ValueError):
    return self.sendj({'connected':True,'items':[],'error':'Les abonnements YouTube sont momentanément indisponibles.'})
  if u.path=='/api/google/status':
   if urllib.parse.parse_qs(u.query).get('check')==['1']:google_access_token()
   client=db("select value from settings where key='google_client_id'")
   with oauth_lock:
    attempt=next(iter(oauth_attempt.values()),{})
    if attempt.get('status')=='pending' and time.monotonic()>attempt['expires']:
     attempt.update(status='error',message='La demande de connexion Google a expiré. Réessaie.')
     attempt.pop('secret',None)
     attempt.pop('verifier',None)
    status=attempt.get('status','idle')
    message=attempt.get('message','')
    connected=bool(keychain('get')) if status not in ('pending','exchanging') else False
    if status in ('idle','connected') and db("select 1 from settings where key='google_reconnect'"):
     status,message,connected='reauthorize',GOOGLE_RECONNECT_MESSAGE,False
   configured=bool(client and client[0]['value'] and keychain('get',name='google-client-secret'))
   return self.sendj({'client_id':client[0]['value'] if client else '', 'configured':configured, 'connected':connected,
                      'status':status,'message':message})
  if u.path=='/api/subtitle':
   f=pathlib.Path(urllib.parse.parse_qs(u.query).get('path',[''])[0]).resolve();allowed=[MOVIES.resolve(),(DATA/'À regarder').resolve()]
   if not f.is_file() or not (any(root in f.parents for root in allowed) or is_local_video(f)):self.send_error(404);return
   candidates=[f.with_name(f.stem+'.fr.vtt'),f.with_suffix('.vtt')];sub=next((p for p in candidates if p.is_file() and not p.is_symlink()),None)
   if not sub:self.send_error(404);return
   self.send_response(200);self.send_header('Content-Type','text/vtt; charset=utf-8');self.end_headers();self.wfile.write(sub.read_bytes());return
  if u.path=='/api/next':
   vid=urllib.parse.parse_qs(u.query).get('id',[''])[0]
   query=up.parse_qs(u.query)
   playlist=playlist_id(query.get('playlist',[''])[0])
   if playlist:
    cached=playlist_entries(active_profile(),playlist)
    try:position=int(query.get('position',['-1'])[0])
    except ValueError:return self.sendj({'next':None})
    entries=cached['items'] if cached else []
    current=next((entry for entry in entries if entry['position']==position),None)
    if not current:return self.sendj({'next':None})
    local=local_youtube_file(current['id']) if vid.startswith('local:') else None
    if vid!=current['id'] and (local is None or vid!='local:'+str(local)):return self.sendj({'next':None})
    following=next((entry for entry in entries if entry['position']>position),None)
    if not following:return self.sendj({'next':None})
    return self.sendj({'next':dict(following,playlist_id=playlist,playlist_position=following['position'],start=(following.get('progress') or {}).get('current',0))})
   if not vid.startswith('local:'):return self.sendj({'next':None})
   f=pathlib.Path(vid[6:]).resolve()
   if not is_local_video(f):return self.sendj({'next':None})
   catalogue=local_catalogue()
   series_root=next((root for path,root,_ in catalogue if path==f),None)
   if series_root is None:return self.sendj({'next':None})
   files=[path for path,root,_ in catalogue if root==series_root]
   try:i=files.index(f)
   except ValueError:return self.sendj({'next':None})
   if i+1>=len(files):return self.sendj({'next':None})
   nxt=files[i+1].resolve();nid='local:'+str(nxt);pr=db('select current,duration from playback_profile where profile=? and id=?',(active_profile(),nid));start=float(pr[0]['current']) if pr and pr[0]['duration']-pr[0]['current']>30 else 0
   return self.sendj({'next':{'id':nid,'path':str(nxt),'title':display_title(nxt),'thumbnail':'/api/thumb?video='+urllib.parse.quote(str(nxt)),'start':start,'media_url':'/api/media?path='+urllib.parse.quote(str(nxt)),'subtitle_url':'/api/subtitle?path='+urllib.parse.quote(str(nxt))}})
  if u.path=='/api/thumb':
   query=up.parse_qs(u.query)
   if 'video' in query:
    source=pathlib.Path(query['video'][0]).resolve()
    f=local_cover(source) if is_local_video(source) else None
   else:
    f=pathlib.Path(query.get('path',[''])[0]).resolve()
    downloaded_cover=f.suffix=='.jpg' and bool(db('select 1 from temporary_downloads where path=?',(str(f.with_suffix('.mp4')),)))
    if not (MOVIES.resolve() in f.parents or (DATA/'jaquettes').resolve() in f.parents or downloaded_cover):f=None
   content_type={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}.get(f.suffix.lower()) if f else None
   if not content_type or not regular_file_size(f):
    self.send_error(404)
    return
   try:cover=f.read_bytes()
   except OSError:return self.send_error(404)
   self.send_response(200);self.send_header('Content-Type',content_type);self.end_headers();self.wfile.write(cover);return
  if u.path=='/api/details':
   params=up.parse_qs(u.query)
   vid=params.get('id',[''])[0]
   if vid.startswith('local:'):
    f=pathlib.Path(vid[6:]).resolve()
    if not is_local_video(f):return self.sendj({'error':'Fichier local introuvable'})
    return self.sendj(self.local_details(f,vid))
   if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):
    return self.sendj({'error':'Fiche vidéo introuvable.'})
   local=local_youtube_file(vid)
   if local is not None:return self.sendj(self.local_details(local,vid))
   downloaded=next(managed_downloads(vid),None)
   if downloaded:return self.sendj(self.downloaded_details(downloaded))
   return self.sendj(self.remote_details(vid,preview=params.get('preview')==['1']))
  if u.path=='/api/search-history':
   return self.sendj(recent_searches(active_profile()))
  if u.path=='/api/search':
   raw_query=' '.join(urllib.parse.parse_qs(u.query).get('q',[''])[0].split())
   q=raw_query[:200]
   profile=active_profile()
   if not q:return self.sendj({'error':'Saisis un titre, un sujet ou un lien YouTube.'})
   video_id,link_error=youtube_link(raw_query[:2048])
   if link_error:return self.sendj({'error':link_error})
   if video_id:
    local=local_youtube_file(video_id)
    if local is not None:
     details=self.local_details(local,'local:'+str(local))
    else:
     download=next(managed_downloads(video_id),None)
     details=self.downloaded_details(download) if download else self.remote_details(video_id)
    if details.get('error'):return self.sendj(details)
    duration=details.get('duration',0)
    live_without_duration=not duration and details.get('youtube_state') in ('is_live','is_upcoming','post_live')
    if (duration<=180 and not live_without_duration) or re.search(r'#shorts|\bshorts\b',details.get('title',''),re.I):
     return self.sendj({'error':'Les vidéos YouTube de 3 minutes ou moins et les Shorts sont masqués.'})
    return self.sendj([details])
   try:raw=subprocess.check_output(['yt-dlp','--extractor-args','youtube:lang=fr','--flat-playlist','--dump-single-json',f'ytsearch30:{q}'],stderr=subprocess.DEVNULL,text=True,timeout=40)
   except Exception as e:return self.sendj({'error':'Recherche YouTube impossible : '+str(e)})
   items=[]
   for entry in json.loads(raw).get('entries') or []:
    if not entry:continue
    video_id=entry.get('id')
    duration=entry.get('duration') or 0
    title=entry.get('title') or 'Vidéo'
    if not video_id or duration<=180 or re.search(r'#shorts|\bshorts\b',title,re.I):continue
    thumbnails=entry.get('thumbnails') or []
    thumbnail=thumbnails[-1].get('url') if thumbnails else None
    items.append({
     'id':video_id,'title':title,'duration':duration,
     'thumbnail':thumbnail or f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
     'channel':entry.get('channel') or entry.get('uploader') or 'YouTube',
     'genre':genre_label(next(iter(entry.get('categories') or []),''))
    })
   add(items)
   remember_search(profile,q,items)
   return self.sendj(items)
  return super().do_GET()
 def do_POST(self):
  if self.headers.get_content_type()!='application/json':return self.send_error(415)
  try:
   length=int(self.headers.get('Content-Length',0))
   if not 0<=length<=1024*1024:raise ValueError
   d=json.loads(self.rfile.read(length)) if length else {}
   if not isinstance(d,dict):raise ValueError
  except ValueError:
   return self.sendj({'ok':False,'error':'Requête invalide.'})
  route=urllib.parse.urlparse(self.path).path
  if route.startswith('/api/google/') and self.headers.get_content_type()!='application/json':
   return self.sendj({'ok':False,'error':'Requête Google invalide.'})
  if route=='/api/storage/clean-covers':
   if self.headers.get_content_type()!='application/json':
    self.send_error(415)
    return
   try:
    return self.sendj(dict(unused_cover_cache(remove=True),ok=True))
   except OSError:
    return self.sendj({'ok':False,'error':'Fauteuil n’a pas pu nettoyer toutes les jaquettes.'})
  if route=='/api/local/unlink':
   if self.headers.get_content_type()!='application/json':
    self.send_error(415)
    return
   path=d.get('path')
   if not isinstance(path,str):
    return self.sendj({'ok':False,'error':'Référence invalide.'})
   with sqlite3.connect(DB) as connection:
    removed=connection.execute('delete from imported_videos where path=?',(path,)).rowcount
   return self.sendj({'ok':bool(removed)} if removed else {'ok':False,'error':'Référence introuvable.'})
  if route=='/api/local/import':
   if not self.native_request():
    self.send_error(403)
    return
   paths=d.get('paths')
   if not isinstance(paths,list) or not 1<=len(paths)<=1000:
    return self.sendj({'ok':False,'error':'Sélectionne entre une et mille vidéos.'})
   return self.sendj(register_local_videos(paths))
  if route=='/api/profiles':
   name=d.get('name','').strip()[:40]
   if not name:return self.sendj({'error':'Donne un nom au profil.'})
   pid=secrets.token_hex(8);avatar=d.get('avatar','🍿')
   with sqlite3.connect(DB) as c:c.execute('insert into profiles values(?,?,?)',(pid,name,avatar));c.execute('insert or replace into settings values(?,?)',('active_profile',pid))
   return self.sendj({'ok':True,'id':pid})
  if route=='/api/profiles/select':
   pid=d.get('id','');exists=db('select id from profiles where id=?',(pid,))
   if not exists:return self.sendj({'error':'Profil introuvable.'})
   with sqlite3.connect(DB) as c:c.execute('insert or replace into settings values(?,?)',('active_profile',pid))
   return self.sendj({'ok':True})
  if route=='/api/profiles/delete':
   pid=d.get('id','')
   if pid=='default':return self.sendj({'error':'Le profil Principal ne peut pas être supprimé.'})
   with sqlite3.connect(DB) as c:c.execute('delete from profiles where id=?',(pid,));c.execute('delete from watchlist_profile where profile=?',(pid,));c.execute('delete from playback_profile where profile=?',(pid,));c.execute('delete from recent_searches where profile=?',(pid,));c.execute('delete from completed_playback where profile=?',(pid,));c.execute('delete from youtube_playlists where profile=?',(pid,));c.execute('delete from youtube_playlist_items where profile=?',(pid,))
   if active_profile()==pid:
    with sqlite3.connect(DB) as c:c.execute('insert or replace into settings values(?,?)',('active_profile','default'))
   return self.sendj({'ok':True})
  if route=='/api/google/setup':
   pages={
    'api':'https://console.cloud.google.com/apis/library/youtube.googleapis.com',
    'branding':'https://console.cloud.google.com/auth/branding',
    'audience':'https://console.cloud.google.com/auth/audience',
    'client':'https://console.cloud.google.com/auth/clients',
   }
   url=pages.get(str(d.get('step','')))
   if url:
    try:
     if webbrowser.open(url):return self.sendj({'ok':True})
    except (OSError,webbrowser.Error):
     pass
   return self.sendj({'ok':False,'error':'Impossible d’ouvrir Google Cloud dans le navigateur.'})
  if route=='/api/google/settings':
   client_id=str(d.get('client_id') or '').strip()
   secret=str(d.get('client_secret') or '').strip()
   if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}\.apps\.googleusercontent\.com',client_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',secret):
    return self.sendj({'ok':False,'error':'Ce fichier ne contient pas les identifiants attendus pour une application de bureau.'})
   with oauth_lock:
    previous=db("select value from settings where key='google_client_id'")
    changed=not previous or previous[0]['value']!=client_id
    if secret and not keychain('set',secret,'google-client-secret'):
     return self.sendj({'ok':False,'error':'Le trousseau macOS n’a pas pu enregistrer le secret client.'})
    if changed or secret:oauth_attempt.clear()
    if changed:
     keychain('delete')
     if not secret:keychain('delete',name='google-client-secret')
     db("delete from settings where key in ('google_access','google_expiry','google_reconnect')",write=True)
    db("insert or replace into settings values('google_client_id',?)",(client_id,),write=True)
   return self.sendj({'ok':True})
  if route=='/api/google/disconnect':
   with oauth_lock:
    oauth_attempt.clear()
    keychain('delete')
    db("delete from settings where key in ('google_access','google_expiry','google_reconnect')",write=True)
   return self.sendj({'ok':True})
  if route=='/api/google/start':
   with oauth_lock:
    client=db("select value from settings where key='google_client_id'")
    secret=keychain('get',name='google-client-secret')
    if not client or not client[0]['value'] or not secret:
     return self.sendj({'error':'La configuration OAuth de Fauteuil est requise une seule fois. Utilise le lien de configuration dans cette fenêtre.'})
    client_id=client[0]['value']
    state=secrets.token_urlsafe(24)
    verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    attempt={'status':'pending','expires':time.monotonic()+OAUTH_TTL,'client_id':client_id,
             'secret':secret,'verifier':verifier}
    oauth_attempt.clear()
    oauth_attempt[state]=attempt
   params={
    'client_id':client_id,'redirect_uri':OAUTH_REDIRECT,'response_type':'code',
    'scope':'https://www.googleapis.com/auth/youtube.readonly','access_type':'offline','prompt':'consent',
    'state':state,'code_challenge':challenge,'code_challenge_method':'S256',
   }
   url='https://accounts.google.com/o/oauth2/v2/auth?'+up.urlencode(params)
   browser='safari' if d.get('browser')=='safari' else None
   def open_google():
    try:
     if webbrowser.get(browser).open(url):return
    except (OSError,webbrowser.Error):
     pass
    with oauth_lock:
     if oauth_attempt.get(state) is attempt and attempt['status']=='pending':
      attempt.update(status='error',message='Impossible d’ouvrir le navigateur pour la connexion Google.')
      attempt.pop('secret',None)
      attempt.pop('verifier',None)
   threading.Thread(target=open_google,daemon=True).start()
   return self.sendj({'ok':True})
  if route=='/api/progress':
   f=pathlib.Path(d.get('path','')).resolve()
   temp=(DATA/'À regarder').resolve()
   ident=d.get('id','local:'+str(f))
   temporary=temp in f.parents
   stream_job=playback_downloads.get(ident) if temporary else None
   stream_state=None
   if stream_job and stream_job['stage']:
    try:
     state=json.loads((stream_job['stage']/'.cinema-stream.json').read_text())
     if pathlib.Path(state['path']).resolve()==f:stream_state=state
    except (OSError,ValueError,KeyError):pass
   if (not f.is_file() and not stream_state) or (not temporary and (not is_local_video(f) or ident!='local:'+str(f))):
    return self.sendj({'ok':False,'error':'Vidéo introuvable.'})
   current=float(d.get('time') or 0)
   duration=float(stream_state['duration'] if stream_state else d.get('duration') or 0)
   if stream_state and (stream_state.get('input_complete') or stream_state.get('complete')):
    try:
     manifest=(stream_job['stage']/'stream/index.m3u8').read_text()
     if '#EXT-X-ENDLIST' in manifest:
      actual_duration=sum(float(value) for value in re.findall(r'#EXTINF:([0-9.]+)',manifest))
      if actual_duration>0:duration=actual_duration
    except OSError:pass
   if not math.isfinite(current) or not math.isfinite(duration) or current<0 or duration<=0:
    return self.sendj({'ok':False,'error':'Position de lecture invalide.'})
   done=(d.get('ended') is True and (not stream_state or current>=duration-2)) or (d.get('closing') is True and duration-current<30)
   if stream_job and (d.get('closing') is True or done):stream_job['detached']=True
   profile=active_profile()
   remove_temporary=False
   with sqlite3.connect(DB) as c:
    c.execute('begin immediate')
    if temporary:
     download=c.execute('select kept from temporary_downloads where id=? and path=?',(ident,str(f))).fetchone()
     if not download and not stream_state:return self.sendj({'ok':False,'error':'Téléchargement introuvable.'})
     remove_temporary=done and (not download or not download[0])
    if done:
     c.execute('delete from playback_profile where profile=? and id=?',(profile,ident))
     c.execute('insert or replace into completed_playback values(?,?,?)',(profile,ident,time.time()))
     if remove_temporary:c.execute('insert or ignore into pending_playback_cleanup values(?,?)',(ident,str(f)))
    else:c.execute('insert or replace into playback_profile values(?,?,?,?,?,?)',(profile,ident,str(f),current,duration,time.time()))
   if remove_temporary:retry_playback_cleanup(ident)
   elif stream_job and stream_job['event'].is_set() and (d.get('closing') is True or done):
    shutil.rmtree(stream_job['stage']/'stream',ignore_errors=True)
   return self.sendj({'ok':True,'completed':done})
  if route.startswith('/api/playlist'):
   profile=active_profile()
   if route=='/api/playlists/sync':return self.sendj(sync_playlists(profile))
   ident=playlist_id(d.get('id') or d.get('url'))
   if not ident:return self.sendj({'error':'Lien de playlist invalide.'})
   if route=='/api/playlist/import':return self.sendj(load_playlist(profile,ident,refresh=True))
   if route=='/api/playlist/remove':
    with sqlite3.connect(DB) as connection:
     connection.execute('delete from youtube_playlist_items where profile=? and playlist=?',(profile,ident))
     connection.execute('delete from youtube_playlists where profile=? and id=?',(profile,ident))
    return self.sendj({'ok':True})
   if route=='/api/playlist/download':
    cached=playlist_entries(profile,ident)
    if not cached:return self.sendj({'error':'Playlist introuvable.'})
    return self.sendj(prepare_trip(profile,d.get('ids'),allowed={entry['id']:entry['title'] for entry in cached['items']}))
   return self.send_error(404)
  if route=='/api/watchlist':
   vid=d.get('id','');
   if d.get('remove'):
    with sqlite3.connect(DB) as c:c.execute('delete from watchlist_profile where profile=? and id=?',(active_profile(),vid))
    return self.sendj({'ok':True,'saved':False})
   row=db('select * from videos where id=?',(vid,))
   if row:
    x=dict(row[0]);thumb=x['thumb'];title=x['title'];duration=x['duration'];channel=x['channel']
   else:x={'title':d.get('title','Vidéo'),'thumbnail':d.get('thumbnail',''),'duration':d.get('duration',0),'channel':d.get('channel','YouTube')};thumb=x['thumbnail'];title=x['title'];duration=x['duration'];channel=x['channel']
   with sqlite3.connect(DB) as c:c.execute('insert or replace into watchlist_profile values(?,?,?,?,?,?,?)',(active_profile(),vid,title,thumb,duration,channel,time.time()))
   return self.sendj({'ok':True,'saved':True})
  if route=='/api/settings':
   client=d.get('google_client_id','').strip()
   with sqlite3.connect(DB) as c:c.execute('insert or replace into settings values(?,?)',('google_client_id',client))
   return self.sendj({'ok':True})
  if route=='/api/download/trip':
   return self.sendj(prepare_trip(active_profile(),d.get('ids')))
  if route=='/api/download/trip/cancel':
   if self.headers.get_content_type()!='application/json':
    self.send_error(415)
    return
   return self.sendj(cancel_trip(active_profile()))
  if route=='/api/download/remove':
   if self.headers.get_content_type()!='application/json':
    self.send_error(415)
    return
   ident=d.get('id')
   if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',ident):
    return self.sendj({'ok':False,'error':'Téléchargement introuvable.'})
   return self.sendj(remove_managed_download(ident))
  if route=='/api/youtube/open':
   ident=d.get('id')
   if self.headers.get_content_type()!='application/json' or not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',ident):
    return self.sendj({'ok':False,'error':'Lien YouTube invalide.'})
   try:
    if webbrowser.open('https://www.youtube.com/watch?v='+ident):return self.sendj({'ok':True})
   except (OSError,webbrowser.Error):
    pass
   return self.sendj({'ok':False,'error':'Impossible d’ouvrir YouTube dans le navigateur.'})
  if route not in ('/api/play','/api/download/keep'):
   self.send_error(404)
   return
  video_id=d.get('id')
  if isinstance(video_id,str) and video_id.startswith('local:'):
   path=pathlib.Path(video_id[6:]).resolve()
   if not is_local_video(path):
    return self.sendj({'ok':False,'error':'Le fichier vidéo local est introuvable.'})
   return self.sendj(playback_source(path))
  if not isinstance(video_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
   return self.sendj({'ok':False,'error':'Identifiant YouTube invalide.'})

  if route=='/api/play':return self.sendj(progressive_source(video_id,start=d.get('start',0),wait_for_file=d.get('wait_for_file') is True))
  return self.sendj(run_download(video_id,keep=True))
 def translate_path(self,p):
  path=urllib.parse.unquote(urllib.parse.urlparse(p).path)
  root=(ROOT/'static').resolve()
  target=(root/(path.lstrip('/') or 'index.html')).resolve()
  return str(target if target.is_relative_to(root) else root/'.not-found')
def serve():
 recover_finished_downloads()
 for entry in managed_downloads():
  stage=entry['path'].relative_to(DATA/'À regarder').parts[0]
  if re.fullmatch(r'[0-9a-f]{16}',stage):shutil.rmtree(DATA/'À regarder'/stage/'stream',ignore_errors=True)
 with http.server.ThreadingHTTPServer(('127.0.0.1',PORT),H) as server:
  def quit_server(_signum,_frame):
   if closing.is_set():return
   closing.set()
   threading.Thread(target=server.shutdown,daemon=True).start()
  signal.signal(signal.SIGTERM,quit_server)
  signal.signal(signal.SIGINT,quit_server)
  threading.Thread(target=cleanup_abandoned_downloads,daemon=True).start()
  server.serve_forever()
  stop_downloads()

if __name__=='__main__':
 serve()
