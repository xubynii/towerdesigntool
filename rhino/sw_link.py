#! python3
# -*- coding: utf-8 -*-
"""
Sun & Wind Link - a Rhino 7 / Rhino 8 panel that prepares a model for Sun & Wind Studio and keeps the tool in step with it.

1. Layers   Creates the layers the studies read (Tower and its parts, Shading, PV, Context, Terrain),
            or maps the layers you already have onto them. Moves selected objects onto a Sun & Wind layer.
2. Check    Scans what will be sent: units, closed volumes, floor plates, ground level, unmapped layers, size.
3. Link     Serves the model on http://127.0.0.1:<port> (and, when this file sits in the tool's rhino/ folder,
            the tool itself), so the browser re-imports on every change. Export JSON writes the same model to a file.

Run it with RunPythonScript (Rhino 7 and 8) or from the Rhino 8 Script Editor. Written for IronPython 2.7 and CPython 3.
Closing the panel stops the link.
"""
from __future__ import print_function, division

import os
import re
import json
import time
import threading
import webbrowser

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
except ImportError:  # IronPython 2.7 (Rhino 7)
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn

import System
import System.Drawing
import Rhino
import Rhino.Geometry as rg
import Rhino.DocObjects as rd
import Rhino.DocObjects.Tables
import scriptcontext as sc
import Eto.Forms as forms
import Eto.Drawing as drawing

VERSION = '1.0'
STICKY = 'sun_wind_link'
DEFAULT_PORT = 8797
MAP_SECTION = 'sw.map'          # document strings: entry = layer id, value = target key
LAYER_KEY = 'sw.target'         # the same key as layer user text, so a saved .3dm carries the mapping
TOOL_URL = 'https://xubynii.github.io/sun-wind-studio/'
ALLOWED_ORIGINS = [             # sites allowed to read the model from this computer
    r'^https?://(127\.0\.0\.1|localhost)(:\d+)?$',
    r'^https://xubynii\.github\.io$',
    r'^https://claude\.ai$',
    r'^https://[a-z0-9.-]+\.claude\.ai$',
    r'^https://[a-z0-9.-]+\.claudeusercontent\.com$',
    r'^null$',                  # a copy opened from disk (file://)
]

# key, label, role, part, standard layer, colour (the tool's part colours)
TARGETS = [
    ('tower',     'Tower: sort parts by shape', 'massing', None,        'Tower',                  (169, 178, 188)),
    ('floor',     'Tower: floor plates',        'massing', 'floor',     'Tower::Floor plates',    (79, 124, 172)),
    ('ground',    'Tower: ground floor',        'massing', 'ground',    'Tower::Ground floor',    (140, 91, 144)),
    ('facade',    'Tower: facade',              'massing', 'facade',    'Tower::Facade',          (126, 138, 150)),
    ('window',    'Tower: windows',             'massing', 'window',    'Tower::Windows',         (95, 168, 211)),
    ('roof',      'Tower: roof',                'massing', 'roof',      'Tower::Roof',            (208, 106, 75)),
    ('ceiling',   'Tower: ceilings',            'massing', 'ceiling',   'Tower::Ceilings',        (217, 164, 65)),
    ('structure', 'Tower: structure',           'massing', 'structure', 'Tower::Structure',       (68, 81, 94)),
    ('core',      'Tower: core',                'massing', 'core',      'Tower::Core',            (176, 125, 79)),
    ('stair',     'Tower: stairs',              'massing', 'stair',     'Tower::Stairs',          (78, 154, 91)),
    ('mass',      'Tower: massing volume',      'massing', 'mass',      'Tower::Massing volume',  (150, 160, 172)),
    ('shading',   'Shading devices',            'shading', None,        'Shading',                (224, 138, 62)),
    ('pv',        'PV surfaces',                'pv',      None,        'PV',                     (46, 64, 112)),
    ('context',   'Site context',               'context', None,        'Context',                (196, 200, 204)),
    ('terrain',   'Terrain',                    'terrain', None,        'Terrain',                (150, 172, 128)),
    ('ignore',    'Ignore: never send',         'ignore',  None,        None,                        None),
]
T = dict((t[0], t) for t in TARGETS)
SHORT = {'tower': 'Tower (auto)', 'floor': 'Floor plates', 'ground': 'Ground floor', 'facade': u'Façade', 'window': 'Windows',
         'roof': 'Roof', 'ceiling': 'Ceilings', 'structure': 'Structure', 'core': 'Core', 'stair': 'Stairs', 'mass': 'Massing',
         'shading': 'Shading', 'pv': 'PV', 'context': 'Context', 'terrain': 'Terrain', 'ignore': 'Ignore'}
KEYS = [t[0] for t in TARGETS]
STD_PATHS = dict((t[4].lower(), t[0]) for t in TARGETS if t[4])
# models set up by v1.0 used 'SW Tower', 'SW Shading', ...: still recognised, and Create layers renames them
LEGACY_ROOTS = dict(('sw ' + t[4].lower(), t[4]) for t in TARGETS if t[4] and '::' not in t[4])
STD_PATHS.update(dict(('sw ' + k, v) for k, v in list(STD_PATHS.items())))

# name rules for Auto-map, checked on the layer's own name first, then on its full path
RULES = [
    (r'topo|terrain|\bdem\b|contour|grade|landform|ground surface', 'terrain'),
    (r'\bpv\b|photovolt|solar panel|bipv|collector', 'pv'),
    (r'shad|\bfins?\b|louv|brise|overhang|canop|screen|sunbreak|balcon|awning', 'shading'),
    (r'ground\s*floor|lobby|\bl0?1\b|level\s*0?1\b|\bgf\b', 'ground'),
    (r'site|context|exist|herit|plaza|road|curb|street|sidewalk|tree|future|surround|neighbo|ground', 'context'),
    (r'stair|step|tread|riser', 'stair'),
    (r'core|shaft|\blift|elev', 'core'),
    (r'column|beam|struct|brace|girder|truss|pier', 'structure'),
    (r'window|glass|glaz|\bpanes?\b|vision', 'window'),
    (r'ceil|soffit', 'ceiling'),
    (r'roof', 'roof'),
    (r'slab|floor|plate|deck|level', 'floor'),
    (r'fa.ade|curtain|cladd|wall|spandrel|mullion|panel|envelope|skin', 'facade'),
    (r'mass|volume|block|tower|building', 'tower'),
]

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json',
        '.wasm': 'application/wasm', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
        '.md': 'text/plain; charset=utf-8', '.ico': 'image/x-icon'}


def say(msg):
    Rhino.RhinoApp.WriteLine('Sun & Wind Link: ' + msg)


def guess_key(name):
    for pat, key in RULES:
        if re.search(pat, name, re.I):
            return key
    return None


def this_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return None


def find_tool_dir():
    """The tool's folder: this file sits in <tool>/rhino/. Remembered once found."""
    cand = []
    remembered = sc.sticky.get(STICKY + '.tool')
    if remembered:
        cand.append(remembered)
    d = this_dir()
    if d:
        cand += [os.path.dirname(d), d]
    for c in cand:
        if c and os.path.isfile(os.path.join(c, 'index.html')) and os.path.isfile(os.path.join(c, 'link.js')):
            sc.sticky[STICKY + '.tool'] = c
            return c
    return None


# ----------------------------------------------------------------------------------------------- model

class Model(object):
    """Reads the active document: layer targets, meshes per object (cached), the payload and the check."""

    def __init__(self):
        self.cache = {}
        self.smooth = False

    @staticmethod
    def doc():
        return Rhino.RhinoDoc.ActiveDoc

    # --- layer targets
    def stored_key(self, layer):
        doc = self.doc()
        k = None
        try:
            k = doc.Strings.GetValue(MAP_SECTION, str(layer.Id))
        except Exception:
            k = None
        if not k:
            try:
                k = layer.GetUserString(LAYER_KEY)
            except Exception:
                k = None
        if not k:
            k = STD_PATHS.get(layer.FullPath.lower())
        return k if k in T else None

    def set_key(self, layer_index, key):
        doc = self.doc()
        layer = doc.Layers[layer_index]
        if key:
            doc.Strings.SetString(MAP_SECTION, str(layer.Id), key)
        else:
            try:
                doc.Strings.Delete(MAP_SECTION, str(layer.Id))
            except Exception:
                pass
        try:
            layer.SetUserString(LAYER_KEY, key or '')
            doc.Layers.Modify(layer, layer_index, True)
        except Exception:
            pass

    def layer_targets(self):
        """layer index -> (key or None, inherited?) for every layer."""
        doc = self.doc()
        own, by_id = {}, {}
        for L in doc.Layers:
            if L.IsDeleted:
                continue
            own[L.Index] = self.stored_key(L)
            by_id[str(L.Id)] = L
        out = {}
        for L in doc.Layers:
            if L.IsDeleted:
                continue
            k, inherited, cur, guard = own.get(L.Index), False, L, 0
            while k is None and guard < 12:
                guard += 1
                pid = str(cur.ParentLayerId)
                cur = by_id.get(pid)
                if cur is None:
                    break
                k = own.get(cur.Index)
                inherited = k is not None
            out[L.Index] = (k, inherited)
        return out

    @staticmethod
    def layer_visible(doc, index):
        L, guard = doc.Layers[index], 0
        while L is not None and guard < 12:
            guard += 1
            if not L.IsVisible:
                return False
            if L.ParentLayerId == System.Guid.Empty:
                return True
            i = doc.Layers.FindId(L.ParentLayerId)
            L = i if isinstance(i, rd.Layer) else None
        return True

    def objects(self):
        doc = self.doc()
        st = rd.ObjectEnumeratorSettings()
        st.NormalObjects = True
        st.LockedObjects = True
        st.HiddenObjects = False
        st.ReferenceObjects = True
        st.IncludeLights = False
        st.IncludeGrips = False
        st.IncludePhantoms = False
        vis = {}
        for o in doc.Objects.GetObjectList(st):
            li = o.Attributes.LayerIndex
            if li not in vis:
                vis[li] = self.layer_visible(doc, li)
            if vis[li]:
                yield o

    # --- meshing
    def mesh_params(self):
        return rg.MeshingParameters.QualityRenderMesh if self.smooth else rg.MeshingParameters.FastRenderMesh

    def mesh_geometry(self, g):
        """Meshes for one piece of geometry (new objects, safe to transform) and its kind, or ([], kind) if unsupported."""
        mp = self.mesh_params()
        if isinstance(g, rg.Mesh):
            return [g.DuplicateMesh()], 'mesh', g.IsClosed
        brep, kind = None, None
        if isinstance(g, rg.Extrusion):
            brep, kind = g.ToBrep(True), 'extrusion'
        elif isinstance(g, rg.Brep):
            brep, kind = g, 'polysurface'
        elif isinstance(g, rg.Surface):
            brep, kind = g.ToBrep(), 'surface'
        elif hasattr(rg, 'SubD') and isinstance(g, rg.SubD):
            m = None
            try:
                m = rg.Mesh.CreateFromSubD(g, 2)
            except Exception:
                m = None
            return ([m] if m else []), 'subd', bool(m and m.IsClosed)
        if brep is None:
            if isinstance(g, (rg.Curve, rg.Point, rg.PointCloud, rg.TextDot)) or 'Annotation' in g.GetType().Name or 'Hatch' in g.GetType().Name:
                return [], 'curve or annotation', False
            return [], g.GetType().Name, False
        ms = rg.Mesh.CreateFromBrep(brep, mp)
        return ([m for m in ms] if ms else []), kind, brep.IsSolid

    def object_meshes(self, obj):
        if isinstance(obj, rd.InstanceObject):
            out, closed = [], True
            stack = [(obj.InstanceDefinition, obj.InstanceXform, 0)]
            while stack:
                idef, xf, depth = stack.pop()
                if idef is None or depth > 8:
                    continue
                for sub in idef.GetObjects():
                    if isinstance(sub, rd.InstanceObject):
                        stack.append((sub.InstanceDefinition, xf * sub.InstanceXform, depth + 1))
                        continue
                    ms, kind, cl = self.mesh_geometry(sub.Geometry)
                    for m in ms:
                        m.Transform(xf)
                        out.append(m)
                    closed = closed and cl
            return out, 'block', closed and bool(out)
        return self.mesh_geometry(obj.Geometry)

    def entry(self, obj, scale):
        """Cached mesh text for one object: v and f strings, triangle count, closed, bounding box (metres)."""
        key = (str(obj.Id), obj.RuntimeSerialNumber, scale, self.smooth)
        hit = self.cache.get(key)
        if hit is not None:
            hit['_used'] = True
            return hit
        ms, kind, closed = self.object_meshes(obj)
        e = {'kind': kind, 'closed': closed, 'ok': False, '_used': True}
        if ms:
            m = rg.Mesh()
            for x in ms:
                if x is not None:
                    m.Append(x)
            m.Faces.ConvertQuadsToTriangles()
            try:
                m.Vertices.CombineIdentical(True, True)
            except Exception:
                pass
            m.Compact()
            if m.Faces.Count:
                pts = m.Vertices.ToPoint3dArray()
                vs = []
                z0, z1 = 1e18, -1e18
                x0, x1, y0, y1 = 1e18, -1e18, 1e18, -1e18
                for p in pts:
                    x, y, z = p.X * scale, p.Y * scale, p.Z * scale
                    vs.append('%.3f,%.3f,%.3f' % (x, y, z))
                    if z < z0: z0 = z
                    if z > z1: z1 = z
                    if x < x0: x0 = x
                    if x > x1: x1 = x
                    if y < y0: y0 = y
                    if y > y1: y1 = y
                f = m.Faces.ToIntArray(True)
                e.update({'ok': True, 'v': ','.join(vs), 'f': ','.join([str(i) for i in f]), 'tris': len(f) // 3,
                          'bb': (x0, y0, z0, x1, y1, z1)})
        self.cache[key] = e
        return e

    # --- what gets sent
    def collect(self):
        """Everything the link and the check need, in one pass."""
        doc = self.doc()
        scale = Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
        targets = self.layer_targets()
        for v in self.cache.values():
            v['_used'] = False
        items, skipped, unmapped = [], [], {}
        for o in self.objects():
            li = o.Attributes.LayerIndex
            key = targets.get(li, (None, False))[0]
            path = doc.Layers[li].FullPath
            if key is None:
                unmapped.setdefault(path, []).append(o.Id)
                continue
            if key == 'ignore':
                continue
            e = self.entry(o, scale)
            if not e['ok']:
                skipped.append((o.Id, path, e['kind']))
                continue
            items.append({'id': o.Id, 'layer': path, 'key': key, 'e': e})
        for k in [k for k, v in self.cache.items() if not v.get('_used')]:
            del self.cache[k]
        return {'scale': scale, 'items': items, 'skipped': skipped, 'unmapped': unmapped,
                'units': str(doc.ModelUnitSystem), 'doc': doc.Name or 'Untitled'}

    def payload(self, c, rev):
        parts = []
        for it in c['items']:
            t = T[it['key']]
            parts.append('{"layer":%s,"role":"%s","part":%s,"kind":"%s","id":"%s","v":[%s],"f":[%s]}' % (
                json.dumps(it['layer']), t[2], json.dumps(t[3]), it['e']['kind'], str(it['id']), it['e']['v'], it['e']['f']))
        head = '{"swlink":"%s","rev":%d,"doc":%s,"units":"m","sourceUnits":%s,"scale":%r,"meshes":[' % (
            VERSION, rev, json.dumps(c['doc']), json.dumps(c['units']), c['scale'])
        return head + ',\n'.join(parts) + ']}'

    # --- the check
    def check(self, c):
        """[(level, short text, [ids], fix or None, tip)] - level is 'error', 'warn', 'info' or 'ok'.
        The text is one short line for the list; the tip explains it when the line is selected."""
        out = []
        items = c['items']
        by = {}
        for it in items:
            by.setdefault(it['key'], []).append(it)
        tower = [it for it in items if T[it['key']][2] == 'massing']
        ids = lambda lst: [it['id'] for it in lst]
        plural = lambda n, w: '%d %s%s' % (n, w, '' if n == 1 else 's')

        if abs(c['scale'] - 1.0) > 1e-9:
            out.append(('info', 'Units: %s, sent as metres' % c['units'].lower(), [], None,
                        'Everything is scaled by %g on the way out. Nothing changes in Rhino.' % c['scale']))
        if not tower:
            out.append(('error', 'No tower', [], None,
                        'Put the tower on the Tower layer, or set one of your layers to a tower part. Every study needs a massing.'))
        if (by.get('shading') or by.get('pv')) and not tower:
            out.append(('error', 'Shading and PV need a tower', ids(by.get('shading', []) + by.get('pv', [])), None,
                        'Devices and panels attach to the tower in the same model.'))
        if c['unmapped']:
            names = sorted(c['unmapped'].keys())
            allids = [i for k in names for i in c['unmapped'][k]]
            out.append(('warn', 'Not sent: %s%s' % (', '.join(names[:3]), ' +%d' % (len(names) - 3) if len(names) > 3 else ''), allids, None,
                        '%s on unmapped layers. Pick what each layer is in Layers, or set it to Ignore.' % plural(len(allids), 'object')))
        if c['skipped']:
            out.append(('info', 'Skipped: %s' % plural(len(c['skipped']), 'curve or annotation'), [s[0] for s in c['skipped']], None,
                        'Only solids, surfaces, meshes, SubDs and blocks are sent.'))

        if tower:
            bb = [it['e']['bb'] for it in tower]
            x0, y0, z0 = min(b[0] for b in bb), min(b[1] for b in bb), min(b[2] for b in bb)
            x1, y1, z1 = max(b[3] for b in bb), max(b[4] for b in bb), max(b[5] for b in bb)
            h, w = z1 - z0, max(x1 - x0, y1 - y0)
            if h > 900 or w > 400:
                out.append(('warn', 'Tower is %.0f m tall: check units' % h, ids(tower), None,
                            'A model drawn in millimetres with the units set to metres comes in 1000 times too big.'))
            elif h < 3:
                out.append(('warn', 'Tower is %.2f m tall: check units' % h, ids(tower), None, 'Options > Units.'))
            open_vol = [it for it in tower if it['key'] in ('tower', 'mass', 'core') and not it['e']['closed']]
            if open_vol:
                out.append(('warn', '%s not closed' % plural(len(open_vol), 'volume'), ids(open_vol), None,
                            'The massing needs closed solids. Cap, Join or MeshRepair them, or move loose surfaces to Facade or Windows.'))
            floors = by.get('floor', []) + by.get('ground', [])
            if floors:
                levels = []
                for z in sorted(it['e']['bb'][5] for it in floors):
                    if not levels or z - levels[-1] > 1.0:
                        levels.append(z)
                thick = [it for it in floors if it['e']['bb'][5] - it['e']['bb'][2] > 1.5]
                if len(levels) < 3:
                    out.append(('warn', 'Only %s' % plural(len(levels), 'floor level'), ids(floors), None,
                                'Three or more plates at storey spacing set the storeys. With fewer, the tool slices the mass at the floor-to-floor height you set.'))
                else:
                    gaps = sorted(b - a for a, b in zip(levels, levels[1:]))
                    out.append(('ok', '%d floors, %.2f m floor to floor' % (len(levels), gaps[len(gaps) // 2]), ids(floors), None,
                                'Read from the floor plates.'))
                if thick:
                    out.append(('warn', '%s over 1.5 m thick' % plural(len(thick), 'plate'), ids(thick), None,
                                'Floor plates should be slabs, one per level.'))
            else:
                out.append(('info', 'No floor plates', [], None,
                            'The tool slices the massing at the floor-to-floor height you set in Design development.'))
            ground = by.get('ground', [])
            gz = max(it['e']['bb'][5] for it in ground) if ground else z0
            if not by.get('terrain') and abs(gz) > 0.5:
                dz = gz / c['scale']
                out.append(('warn', 'Ground at z %.2f m' % gz, ids(ground or tower), lambda dz=dz: self.move_all(c, dz),
                            'The tool reads z = 0 as the ground; anything below becomes basement. Fix moves every sent object by %.2f m (one undo).' % -gz))
            elif ground:
                out.append(('ok', 'Ground at z 0', ids(ground), None, 'The ground floor sits on the site ground.'))
            far = math_hypot((x0 + x1) / 2, (y0 + y1) / 2)
            if far > 2000:
                out.append(('info', '%.1f km from origin' % (far / 1000.0), ids(tower), None,
                            'The tool moves the tower onto the parcel unless the model uses the Vancouver base model coordinates.'))
            if not by.get('window') and (by.get('facade') or by.get('floor')):
                out.append(('info', 'No windows', [], None, 'The window-to-wall ratio is set in the tool instead.'))

        tris = sum(it['e']['tris'] for it in items)
        if tris > 1500000:
            out.append(('warn', '%s triangles: slow updates' % format(tris, ','), [], None,
                        'Use Fast meshes, simplify the context or hide layers you do not need.'))
        if not any(o[0] in ('error', 'warn') for o in out) and tower:
            counts = ', '.join('%d %s' % (len(by[k]), SHORT[k].lower()) for k in KEYS if k in by)
            out.insert(0, ('ok', 'Ready to send', ids(items), None, '%s. %s triangles.' % (counts, format(tris, ','))))
        return out

    def move_all(self, c, dz):
        doc = self.doc()
        xf = rg.Transform.Translation(0, 0, -dz)
        sn = doc.BeginUndoRecord('Sun & Wind: ground to z = 0')
        n = 0
        try:
            for it in c['items']:
                if doc.Objects.Transform(it['id'], xf, True) != System.Guid.Empty:
                    n += 1
        finally:
            doc.EndUndoRecord(sn)
        doc.Views.Redraw()
        say('moved %d objects by %.3f (model units)' % (n, -dz))


def math_hypot(a, b):
    return (a * a + b * b) ** 0.5


# ----------------------------------------------------------------------------------------------- server

class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _origin_ok(self):
        o = self.headers.get('Origin')
        if not o:
            return None
        for pat in ALLOWED_ORIGINS + list(self.server.link.extra_origins):
            if re.match(pat, o):
                return o
        return False

    def _head(self, code, ctype, length):
        self.send_response(code)
        o = self._origin_ok()
        if o:
            self.send_header('Access-Control-Allow-Origin', o)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', '*')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cache-Control', 'no-store')
        if ctype:
            self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.end_headers()

    def do_OPTIONS(self):
        self._head(204, None, 0)

    def _send(self, code, ctype, body, raw=False):
        # raw: file bytes as read ('rb'). IronPython reads them as a str of byte values; encoding again would corrupt them.
        if not raw and not isinstance(body, bytes):
            body = body.encode('utf-8')
        self._head(code, ctype, len(body))
        self.wfile.write(body)

    def do_GET(self):
        try:
            link = self.server.link
            path = self.path.split('?')[0].split('#')[0]
            if path.startswith('/sw/') and self._origin_ok() is False:
                return self._send(403, 'text/plain', 'This site is not allowed to read the Rhino model. Add it under Allowed sites in Sun & Wind Link.')
            link.last_poll = time.time()
            if path == '/sw/rev':
                return self._send(200, 'application/json', json.dumps({'rev': link.rev, 'doc': link.doc_name, 'objects': link.count, 'version': VERSION}))
            if path == '/sw/model':
                return self._send(200, 'application/json', link.body or '{"swlink":"%s","rev":0,"meshes":[]}' % VERSION)
            tool = link.tool_dir
            if not tool:
                return self._send(404, 'text/plain', 'Sun & Wind Link is running; the tool folder was not found next to sw_link.py.')
            rel = path.lstrip('/') or 'index.html'
            full = os.path.normpath(os.path.join(tool, *rel.split('/')))
            if not full.startswith(os.path.normpath(tool) + os.sep) or not os.path.isfile(full):
                return self._send(404, 'text/plain', 'Not found')
            with open(full, 'rb') as fh:
                data = fh.read()
            self._send(200, MIME.get(os.path.splitext(full)[1].lower(), 'application/octet-stream'), data, raw=True)
        except Exception as ex:  # a closed browser tab, mostly
            try:
                self._send(500, 'text/plain', str(ex))
            except Exception:
                pass

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except Exception:
            pass


# ----------------------------------------------------------------------------------------------- link

class Link(object):
    def __init__(self):
        self.model = Model()
        self.server = None
        self.thread = None
        self.port = DEFAULT_PORT
        self.rev = 0
        self.body = ''
        self.count = 0
        self.doc_name = ''
        self.dirty = True
        self.changed_at = 0.0
        self.layers_dirty = False
        self.check_dirty = True
        self.last_poll = 0.0
        self.last_build = 0.0
        self.build_ms = 0
        self.mute = False
        self.tool_dir = find_tool_dir()
        self.extra_origins = []
        self.ui = None
        self.handlers = []

    # --- events
    def touch(self, layers=False):
        self.dirty = True
        self.check_dirty = True
        self.changed_at = time.time()
        if layers:
            self.layers_dirty = True

    def _changed(self, sender, e):
        self.touch(layers=not self.mute)

    def _layers(self, sender, e):
        self.touch(layers=not self.mute)

    def _doc(self, sender, e):
        self.model.cache = {}
        self._layers(sender, e)

    def _idle(self, sender, e):
        try:
            if self.ui and self.layers_dirty and time.time() - self.changed_at > 0.4:
                self.layers_dirty = False
                self.ui.refresh_layers()
            if self.server and self.dirty and time.time() - self.changed_at > 0.5:
                self.build()
            if self.ui and self.check_dirty:
                if self.ui.tabs.SelectedIndex == 1:
                    if time.time() - self.changed_at > 0.6:
                        self.ui.run_check()
                elif self.ui.tabs.Pages[1].Text != 'Check':
                    self.ui.tabs.Pages[1].Text = 'Check'  # the count is stale until the tab is opened again
            if self.ui and time.time() - getattr(self, '_tick', 0) > 1.0:
                self._tick = time.time()
                self.ui.show_status()
                self.ui.tick()
        except Exception as ex:
            say('idle error: %s' % ex)

    def hook(self):
        if self.handlers:
            return
        D = Rhino.RhinoDoc
        pairs = [
            ('AddRhinoObject', rd.RhinoObjectEventArgs, self._changed),
            ('DeleteRhinoObject', rd.RhinoObjectEventArgs, self._changed),
            ('UndeleteRhinoObject', rd.RhinoObjectEventArgs, self._changed),
            ('ReplaceRhinoObject', rd.RhinoReplaceObjectEventArgs, self._changed),
            ('ModifyObjectAttributes', rd.RhinoModifyObjectAttributesEventArgs, self._changed),
            ('LayerTableEvent', rd.Tables.LayerTableEventArgs, self._layers),
            ('InstanceDefinitionTableEvent', rd.Tables.InstanceDefinitionTableEventArgs, self._changed),
            ('DocumentPropertiesChanged', Rhino.DocumentEventArgs, self._doc),
            ('EndOpenDocument', Rhino.DocumentOpenEventArgs, self._doc),
            ('NewDocument', Rhino.DocumentEventArgs, self._doc),
        ]
        for name, args, fn in pairs:
            try:
                h = System.EventHandler[args](fn)
                ev = getattr(D, name)
                ev += h
                self.handlers.append((name, h))
            except Exception as ex:
                say('could not watch %s (%s)' % (name, ex))
        try:
            self.idle_h = System.EventHandler(self._idle)
            Rhino.RhinoApp.Idle += self.idle_h
        except Exception as ex:
            say('could not hook idle (%s)' % ex)

    def unhook(self):
        D = Rhino.RhinoDoc
        for name, h in self.handlers:
            try:
                ev = getattr(D, name)
                ev -= h
            except Exception:
                pass
        self.handlers = []
        try:
            Rhino.RhinoApp.Idle -= self.idle_h
        except Exception:
            pass

    # --- payload
    def build(self):
        t0 = time.time()
        c = self.model.collect()
        new_body = self.model.payload(c, 0)
        if new_body != getattr(self, '_last_plain', None):
            self._last_plain = new_body
            self.rev += 1
            self.body = self.model.payload(c, self.rev)
        self.count = len(c['items'])
        self.doc_name = c['doc']
        self.dirty = False
        self.last_build = time.time()
        self.build_ms = int((time.time() - t0) * 1000)
        return c

    # --- server
    def start(self, port):
        if self.server:
            return True
        try:
            srv = _Server(('127.0.0.1', int(port)), _Handler)
        except Exception as ex:
            say('port %s is busy or blocked (%s). Pick another port.' % (port, ex))
            return False
        srv.link = self
        self.port = int(port)
        self.server = srv
        self.build()
        self.thread = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.3})
        self.thread.daemon = True
        self.thread.start()
        say('serving on http://127.0.0.1:%d (rev %d, %d objects, %d ms)' % (self.port, self.rev, self.count, self.build_ms))
        return True

    def stop(self):
        srv, self.server = self.server, None
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
            say('link stopped')

    def tool_url(self):
        if self.server and self.tool_dir:
            return 'http://127.0.0.1:%d/?link' % self.port
        return TOOL_URL + '?link=%d' % self.port


# ----------------------------------------------------------------------------------------------- panel

GREY = (178, 186, 194)
GREEN = (60, 154, 95)
AMBER = (217, 164, 65)
RED = (200, 85, 61)
MARK = {'error': u'✕  ', 'warn': u'!   ', 'info': u'·   ', 'ok': u'✓  '}


def enum_none(E):
    """The member named None of a .NET enum: IronPython calls it None, Python.NET None_ (or it is 0)."""
    for n in ('None', 'None_'):
        try:
            return getattr(E, n)
        except AttributeError:
            pass
    return System.Enum.ToObject(E, 0)


def color(rgb):
    return drawing.Color.FromArgb(rgb[0], rgb[1], rgb[2], 255)


def label(text, muted=False, bold=False, wrap=False, tip=None):
    l = forms.Label()
    l.Text = text
    if bold:
        try:
            l.Font = drawing.Fonts.Sans(l.Font.Size, drawing.FontStyle.Bold, enum_none(drawing.FontDecoration))
        except Exception:
            pass
    if muted:
        l.TextColor = drawing.Colors.Gray
    if wrap:
        l.Wrap = forms.WrapMode.Word
    if tip:
        l.ToolTip = tip
    l.VerticalAlignment = forms.VerticalAlignment.Center
    return l


def button(text, fn, tip=None):
    b = forms.Button()
    b.Text = text
    if tip:
        b.ToolTip = tip
    b.Click += (lambda s, e: guarded(fn))
    return b


def guarded(fn):
    try:
        fn()
    except Exception as ex:
        say('error: %s' % ex)
        import traceback
        traceback.print_exc()


def hrow(controls, scale=None, spacing=6):
    """One row of controls; the control at index `scale` (or a None spacer) takes the spare width."""
    t = forms.TableLayout()
    t.Spacing = drawing.Size(spacing, 0)
    r = forms.TableRow()
    for i, c in enumerate(controls):
        cell = forms.TableCell()
        if c is not None:
            cell.Control = c
        cell.ScaleWidth = (i == scale) or c is None
        r.Cells.Add(cell)
    t.Rows.Add(r)
    return t


def vbox(controls, scale=None, padding=0, spacing=8):
    """Controls stacked; the one at index `scale` takes the spare height."""
    t = forms.TableLayout()
    t.Padding = drawing.Padding(padding)
    t.Spacing = drawing.Size(0, spacing)
    for i, c in enumerate(controls):
        r = forms.TableRow()
        cell = forms.TableCell()
        cell.Control = c
        cell.ScaleWidth = True
        r.Cells.Add(cell)
        r.ScaleHeight = (i == scale)
        t.Rows.Add(r)
    return t


class Panel(object):
    def __init__(self, link):
        self.link = link
        self.issues = []
        self.note_at = 0.0
        f = forms.Form()
        f.Title = 'Sun & Wind Link'
        f.ClientSize = drawing.Size(340, 540)
        f.MinimumSize = drawing.Size(300, 420)
        f.Resizable = True
        f.Minimizable = True
        try:
            f.Owner = Rhino.UI.RhinoEtoApp.MainWindow
        except Exception:
            pass
        self.form = f

        # link bar: state on the left, the two actions on the right
        self.dot = label(u'●')
        self.state = label('Off', bold=True)
        self.detail = label('', muted=True)
        self.go = button('Start', self.toggle, 'Serve this model to Sun & Wind Studio. Only this computer can connect.')
        self.open_btn = button('Open tool', self.open_tool, 'Open the tool, already linked to this model.')
        top = hrow([self.dot, self.state, self.detail, None, self.go, self.open_btn], spacing=5)

        # Layers tab
        self.layer_box = forms.Scrollable()
        try:
            self.layer_box.Border = enum_none(forms.BorderType)
        except Exception:
            pass
        self.move_dd = forms.DropDown()
        for t in TARGETS[:-1]:
            self.move_dd.Items.Add(SHORT[t[0]])
        self.move_dd.SelectedIndex = 0
        layers_page = vbox([
            hrow([button('Create layers', self.create_layers, 'Add the Tower, Shading, PV, Context and Terrain layers.'),
                  button('Auto-map', self.auto_map, 'Set your layers by their names, e.g. A-GLAZ to Windows.'), None]),
            self.layer_box,
            hrow([label('Selected to', muted=True), self.move_dd, button('Move', self.move_selected, 'Move the selected objects onto that layer.')], scale=1),
        ], scale=1, padding=8)

        # Check tab
        self.issue_list = forms.ListBox()
        self.issue_list.SelectedIndexChanged += (lambda s, e: guarded(self.select_issue))
        self.tip = label('', muted=True, wrap=True)
        self.fix_btn = button('Fix', self.fix_issue)
        self.fix_btn.Visible = False
        check_page = vbox([self.issue_list, self.tip, hrow([None, self.fix_btn])], scale=0, padding=8)

        self.tabs = forms.TabControl()
        for title, content in (('Layers', layers_page), ('Check', check_page)):
            p = forms.TabPage()
            p.Text = title
            p.Content = content
            self.tabs.Pages.Add(p)
        self.tabs.SelectedIndexChanged += (lambda s, e: guarded(self.on_tab))

        # settings, folded away
        self.port = forms.NumericStepper()
        self.port.MinValue = 1024
        self.port.MaxValue = 65535
        self.port.DecimalPlaces = 0
        self.port.Value = link.port
        self.quality = forms.DropDown()
        self.quality.Items.Add('Fast')
        self.quality.Items.Add('Smooth')
        self.quality.ToolTip = 'Smooth suits curved facades but makes updates heavier.'
        self.quality.SelectedIndex = 1 if link.model.smooth else 0
        self.quality.SelectedIndexChanged += (lambda s, e: guarded(self.set_quality))
        more = forms.Expander()
        more.Header = label('Settings', muted=True)
        more.Expanded = False
        self.more = more
        more.Content = vbox([
            hrow([label('Port', muted=True), self.port, label('Meshes', muted=True), self.quality, None]),
            hrow([button('Export JSON...', self.export, 'Save the model as a file the tool can open.'), None]),
        ], padding=4, spacing=6)

        self.status = label('', muted=True, wrap=True)   # short notes after an action
        f.Content = vbox([top, self.tabs, more, self.status], scale=1, padding=10)
        f.Closed += (lambda s, e: guarded(self.closed))
        self.show_status()
        self.refresh_layers()

    # --- notes
    def note(self, text):
        self.status.Text = text
        self.note_at = time.time()
        say(text)

    def tick(self):
        if self.status.Text and time.time() - self.note_at > 6:
            self.status.Text = ''

    # --- layers
    def refresh_layers(self):
        doc = self.link.model.doc()
        targets = self.link.model.layer_targets()
        counts = {}
        for o in self.link.model.objects():
            li = o.Attributes.LayerIndex
            counts[li] = counts.get(li, 0) + 1
        rows = []
        for L in doc.Layers:
            if L.IsDeleted:
                continue
            key, inh = targets.get(L.Index, (None, False))
            own = self.link.model.stored_key(L)
            if counts.get(L.Index):  # only layers with visible objects; empty standard layers stay out of the way
                rows.append((L, own, key if inh else None))
        rows.sort(key=lambda r: r[0].FullPath.lower())
        table = forms.TableLayout()
        table.Spacing = drawing.Size(6, 2)
        if not rows:
            r = forms.TableRow()
            cell = forms.TableCell()
            cell.Control = label('No objects yet. Create layers, or draw on your own.', muted=True, wrap=True)
            r.Cells.Add(cell)
            table.Rows.Add(r)
        for L, own, parent_key in rows:
            shown = own or parent_key
            dot = label(u'■')
            dot.TextColor = color(T[shown][5] if shown and T[shown][5] else GREY)
            depth = L.FullPath.count('::')
            n = counts.get(L.Index, 0)
            name = label('    ' * depth + L.Name, muted=not shown, tip='%s\n%s' % (L.FullPath, '%d object%s' % (n, '' if n == 1 else 's') if n else 'empty'))
            dd = forms.DropDown()
            dd.Items.Add((u'↳ ' + SHORT[parent_key]) if parent_key else u'—')
            for t in TARGETS:
                dd.Items.Add(SHORT[t[0]])
            dd.SelectedIndex = (KEYS.index(own) + 1) if own else 0
            dd.Width = 124
            dd.SelectedIndexChanged += self._on_map(L.Index)
            r = forms.TableRow()
            for c, sw in ((dot, False), (name, True), (dd, False)):
                cell = forms.TableCell()
                cell.Control = c
                cell.ScaleWidth = sw
                r.Cells.Add(cell)
            table.Rows.Add(r)
        spare = forms.TableRow()
        spare.ScaleHeight = True  # spare height goes below the rows
        table.Rows.Add(spare)
        self.layer_box.Content = table

    def _on_map(self, index):
        def fn(sender, e):
            def go():
                i = sender.SelectedIndex
                key = KEYS[i - 1] if i > 0 else None
                self.link.mute = True
                try:
                    self.link.model.set_key(index, key)
                finally:
                    self.link.mute = False
                self.link.touch(layers=True)
            guarded(go)
        return fn

    def ensure_layer(self, key):
        doc = self.link.model.doc()
        t = T[key]
        path = t[4]
        idx = doc.Layers.FindByFullPath(path, -1)
        if idx >= 0:
            return idx
        parent = None
        if '::' in path:
            parent = self.ensure_layer('tower')
        L = rd.Layer()
        L.Name = path.split('::')[-1]
        L.Color = System.Drawing.Color.FromArgb(t[5][0], t[5][1], t[5][2])
        if parent is not None:
            L.ParentLayerId = doc.Layers[parent].Id
        idx = doc.Layers.Add(L)
        self.link.mute = True
        try:
            self.link.model.set_key(idx, key)
        finally:
            self.link.mute = False
        return idx

    def create_layers(self):
        doc = self.link.model.doc()
        renamed = self.rename_legacy()
        before = doc.Layers.Count
        for t in TARGETS:
            if t[4]:
                self.ensure_layer(t[0])
        self.refresh_layers()
        added = doc.Layers.Count - before
        parts = (['%d added' % added] if added else []) + (['%d renamed' % renamed] if renamed else [])
        self.note('Layers: ' + ', '.join(parts) if parts else 'Layers ready')

    def rename_legacy(self):
        """'SW Tower' and friends (v1.0) become 'Tower' etc., unless a layer of the new name already exists."""
        doc = self.link.model.doc()
        n = 0
        for L in list(doc.Layers):
            if L.IsDeleted or L.ParentLayerId != System.Guid.Empty:
                continue
            new = LEGACY_ROOTS.get(L.Name.lower())
            if new and doc.Layers.FindByFullPath(new, -1) < 0:
                L.Name = new
                self.link.mute = True
                try:
                    doc.Layers.Modify(L, L.Index, True)
                finally:
                    self.link.mute = False
                n += 1
        return n

    def auto_map(self):
        doc = self.link.model.doc()
        targets = self.link.model.layer_targets()
        n, left = 0, []
        used = set(o.Attributes.LayerIndex for o in self.link.model.objects())
        for L in doc.Layers:
            if L.IsDeleted or L.Index not in used or targets.get(L.Index, (None, False))[0]:
                continue
            k = guess_key(L.Name) or guess_key(L.FullPath)
            if k:
                self.link.mute = True
                try:
                    self.link.model.set_key(L.Index, k)
                finally:
                    self.link.mute = False
                n += 1
            else:
                left.append(L.FullPath)
        self.link.touch()
        self.refresh_layers()
        self.note('%d mapped%s' % (n, (', still unset: ' + ', '.join(left[:3]) + (' ...' if len(left) > 3 else '')) if left else ''))

    def move_selected(self):
        doc = self.link.model.doc()
        key = KEYS[self.move_dd.SelectedIndex]
        sel = list(doc.Objects.GetSelectedObjects(False, False))
        if not sel:
            self.note('Select objects in Rhino first')
            return
        idx = self.ensure_layer(key)
        sn = doc.BeginUndoRecord('Sun & Wind: move to layer')
        try:
            for o in sel:
                a = o.Attributes.Duplicate()
                a.LayerIndex = idx
                doc.Objects.ModifyAttributes(o, a, True)
        finally:
            doc.EndUndoRecord(sn)
        doc.Views.Redraw()
        self.refresh_layers()
        self.note('Moved %d to %s' % (len(sel), SHORT[key]))

    # --- check
    def on_tab(self):
        if self.tabs.SelectedIndex == 1:
            self.run_check()

    def run_check(self):
        c = self.link.build() if self.link.server else self.link.model.collect()
        self.issues = self.link.model.check(c)
        self.link.check_dirty = False
        keep = self.issue_list.SelectedIndex
        self._filling = True
        try:
            self.issue_list.Items.Clear()
            for lv, text, ids, fix, tip in self.issues:
                self.issue_list.Items.Add(MARK[lv] + text)
        finally:
            self._filling = False
        bad = sum(1 for i in self.issues if i[0] in ('error', 'warn'))
        self.tabs.Pages[1].Text = ('Check (%d)' % bad) if bad else u'Check ✓'
        if 0 <= keep < len(self.issues):
            self.issue_list.SelectedIndex = keep
        else:
            self.tip.Text = 'Select a line to see it in the model.' if self.issues else ''
            self.fix_btn.Visible = False

    def _issue(self):
        i = self.issue_list.SelectedIndex
        return self.issues[i] if 0 <= i < len(self.issues) else None

    def select_issue(self):
        it = self._issue()
        self.fix_btn.Visible = bool(it and it[3])
        self.tip.Text = it[4] if it else ''
        if not it or getattr(self, '_filling', False):
            return
        doc = self.link.model.doc()
        doc.Objects.UnselectAll()
        for i in it[2]:
            doc.Objects.Select(i, True)
        doc.Views.Redraw()

    def fix_issue(self):
        it = self._issue()
        if not it or not it[3]:
            return
        it[3]()
        self.issue_list.SelectedIndex = -1
        self.run_check()
        self.note('Fixed')

    # --- link
    def toggle(self):
        if self.link.server:
            self.link.stop()
        else:
            self.link.start(int(self.port.Value))
        self.show_status()

    def show_status(self):
        L = self.link
        self.go.Text = 'Stop' if L.server else 'Start'
        self.port.Enabled = not L.server
        if not L.server:
            self.dot.TextColor = color(GREY)
            self.state.Text = 'Off'
            self.detail.Text = ''
            return
        linked = time.time() - L.last_poll < 3
        self.dot.TextColor = color(GREEN if linked else AMBER)
        self.state.Text = 'Live' if linked else 'Waiting'
        self.detail.Text = '%d objects' % L.count
        self.detail.ToolTip = 'http://127.0.0.1:%d  rev %d  built in %d ms' % (L.port, L.rev, L.build_ms)

    def open_tool(self):
        if not self.link.server:
            self.link.start(int(self.port.Value))
            self.show_status()
        url = self.link.tool_url()
        if not self.link.tool_dir:
            say('tool folder not found next to this script; opening %s' % url)
        try:
            webbrowser.open(url)
        except Exception:
            psi = System.Diagnostics.ProcessStartInfo(url)
            psi.UseShellExecute = True
            System.Diagnostics.Process.Start(psi)

    def set_quality(self):
        self.link.model.smooth = self.quality.SelectedIndex == 1
        self.link.touch()

    def export(self):
        dlg = forms.SaveFileDialog()
        dlg.Title = 'Export for Sun & Wind Studio'
        dlg.Filters.Add(forms.FileFilter('Sun & Wind model (*.json)', '.json'))
        doc = self.link.model.doc()
        try:
            dlg.FileName = os.path.splitext(doc.Name or 'model')[0] + '-sunwind.json'
        except Exception:
            pass
        if dlg.ShowDialog(self.form) != forms.DialogResult.Ok:
            return
        c = self.link.model.collect()
        body = self.link.model.payload(c, 0)
        path = dlg.FileName if dlg.FileName.lower().endswith('.json') else dlg.FileName + '.json'
        with open(path, 'wb') as fh:
            fh.write(body.encode('utf-8'))
        self.note('Exported %d objects' % len(c['items']))

    def closed(self):
        self.link.stop()
        self.link.unhook()
        self.link.ui = None
        sc.sticky.pop(STICKY, None)


def main():
    old = sc.sticky.get(STICKY)
    if old is not None and old.ui is not None:
        try:
            old.ui.form.Show()
            old.ui.form.BringToFront()
            return
        except Exception:
            pass
    link = Link()
    link.hook()
    link.ui = Panel(link)
    sc.sticky[STICKY] = link
    link.ui.form.Show()
    say('panel open (v%s). Tool folder: %s' % (VERSION, link.tool_dir or 'not found, Open the tool uses ' + TOOL_URL))


if __name__ == '__main__':
    main()
