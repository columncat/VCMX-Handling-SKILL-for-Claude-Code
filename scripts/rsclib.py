# -*- coding: utf-8 -*-
"""rsclib — Visual Components layout.rsc editing library.

A .vcmx is a zip; inside, `layout.rsc` is the model. This library parses and edits it
safely. Import from a scratch script:  `import sys; sys.path.insert(0, r"<skill>\\scripts"); import rsclib as R`

Key facts encoded here (see references/ for full explanation):
- Outer structure uses LF. Inside `ProgramData "..."` the XML is ESCAPED: quotes are `\\"`,
  line breaks are literal `\\r`/`\\n` (2 chars), and each wrapped line starts with a real CR.
  So a statement separator = real-CR + literal `\\n` + spaces. Derive it, never hardcode.
- Read/write with encoding='utf-8', newline='' (no translation). Schedule CSVs: utf-8-sig.
- Non-ASCII (Korean) paths break inline in shells — discover with glob (see find_one).
"""
import re, glob, os, shutil, zipfile
import xml.dom.minidom as minidom

Q = '\\"'                      # a double-quote inside escaped ProgramData XML (backslash+quote)

# ---------- io / paths ----------
def load(path): return open(path, 'r', encoding='utf-8', newline='').read()
def save(path, s): open(path, 'w', encoding='utf-8', newline='').write(s)
def find_one(pattern):
    h = glob.glob(pattern); assert len(h) == 1, (pattern, h); return h[0]

# ---------- core regexes ----------
PROG = re.compile(r'ProgramData "((?:[^"\\]|\\.)*)"')          # group(1)=inner escaped XML
PROC = re.compile(r'<process name=\\"([^\\]*)\\"')             # process name inside inner
NODE = re.compile(r'Node "rSimResource"\n\{\nName "([^"]*)"')  # a component node
STMT = re.compile(r'<statement type=\\"([A-Za-z]+)\\">(.*?)</statement>', re.DOTALL)  # flat; ok for counts
def leaf(kind):   # matches ONE leaf statement of a type (no nested <statement>); use for IWork/ITransport*/signals
    return re.compile(r'<statement type=\\"%s\\">(?:(?!</statement>).)*?</statement>' % kind, re.DOTALL)

def sep10(raw):  # statement separator (real-CR + literal \n + 10 spaces). derive, don't hardcode
    return re.search(r'<statements>(\s*\\n\s*)<statement ', raw).group(1)

# ---------- property read/write inside an escaped statement ----------
def prop(stmt, name):
    m = re.search(r'name=\\"%s\\" value=\\"(.*?)\\"' % name, stmt); return m.group(1) if m else None
def set_prop(stmt, name, value):
    tgt = 'name=' + Q + name + Q + ' value=' + Q
    pat = re.escape(tgt) + r'[^"\\]*' + re.escape(Q)
    new, n = re.subn(pat, lambda m: tgt + value + Q, stmt, count=1)
    assert n == 1, ("set_prop failed (prop missing or has no value=)", name)
    return new
def has_empty_signal(stmt):   # dangling: <property name="Signal" isvisible=...> with NO value=
    return re.search(re.escape('name=' + Q + 'Signal' + Q) + r'\s+isvisible=', stmt) is not None

# ---------- blocks & nodes ----------
def blocks(raw):
    """process name -> (abs_start_of_inner, inner_string)."""
    d = {}
    for pm in PROG.finditer(raw):
        inner = pm.group(1); pn = PROC.search(inner)
        if pn: d[pn.group(1)] = (pm.start(1), inner)
    return d
def node_marks(raw):
    """[(abs_start, name)] for each rSimResource node, in file order."""
    return [(m.start(), m.group(1)) for m in NODE.finditer(raw)]
def node_span(raw, marks, i):
    starts = [p for p, _ in marks] + [len(raw)]; return marks[i][0], starts[i + 1]
def owner_of(marks, pos):
    """owning node name of a ProgramData at abs position pos = nearest preceding node."""
    st = None
    for p, n in marks:
        if p < pos: st = n
        else: break
    return st
def process_owner_map(raw):
    """process name -> owning node name."""
    marks = node_marks(raw); out = {}
    for pm in PROG.finditer(raw):
        pn = PROC.search(pm.group(1))
        if pn: out[pn.group(1)] = owner_of(marks, pm.start())
    return out

# ---------- statement templates (extracted from the file so escaping is exact) ----------
def wait_template(raw): return leaf("IWaitSignalStatement").search(raw).group(0)
def send_template(raw):
    for m in leaf("ISendSignalStatement").finditer(raw):
        if ('name=' + Q + 'Value' + Q) in m.group(0): return m.group(0)  # a bool-style send
    return leaf("ISendSignalStatement").search(raw).group(0)
def make_wait(tpl, component, signal):
    s = set_prop(tpl, 'Component', component); s = set_prop(s, 'Signal', signal); s = set_prop(s, 'WaitTrigger', 'True')
    for k in ('Condition', 'TimedOutVariableName', 'ValueOutputVariableName'):
        try: s = set_prop(s, k, '')
        except AssertionError: pass
    return set_prop(s, 'ExecutedCycleTime', '0')
def make_send(tpl, component, signal, value):
    s = set_prop(tpl, 'Component', component); s = set_prop(s, 'Signal', signal); s = set_prop(s, 'Value', value)
    return set_prop(s, 'ExecutedCycleTime', '0')

# ---------- locate a statement inside an inner block ----------
def find_stmt(inner, stype, occ=1):
    """return (open_pos, close_end) of the occ-th (1-based) <statement type=stype> (leaf types)."""
    opens = [m.start() for m in re.finditer(re.escape('<statement type=' + Q + stype + Q + '>'), inner)]
    assert len(opens) >= occ, (stype, occ, len(opens))
    op = opens[occ - 1]; ce = inner.index('</statement>', op) + len('</statement>')
    return op, ce
def statements_open_marker(raw):  return '<statements>' + sep10(raw)             # start of a real statements list

# ---------- op-based editing (collect ops on the WHOLE raw, apply once) ----------
# op = (start, end, text): replace raw[start:end] with text. Insertion => start==end.
def apply_ops(raw, ops):
    oss = sorted(ops, key=lambda o: (o[0], o[1]))
    for a, b in zip(oss, oss[1:]):
        if a[0] < b[1] and b[0] < a[1]: raise SystemExit(("OVERLAP", a, b))
    out = raw
    # end-first; at equal start, larger-end (a replace) before a zero-width insert
    for s, e, txt in sorted(ops, key=lambda o: (-o[0], -o[1])):
        out = out[:s] + txt + out[e:]
    return out
def op_insert_before(base, inner, op_pos, stmts, SEP):   # insert stmts (list) before a statement at inner-offset op_pos
    idx = base + op_pos; return (idx, idx, SEP.join(stmts) + SEP)
def op_insert_after(base, inner, ce_pos, stmts, SEP):
    idx = base + ce_pos; return (idx, idx, SEP + SEP.join(stmts))
def op_prepend_first(base, inner, stmts, raw):           # insert as first statement(s) of a process
    OPEN = statements_open_marker(raw); assert inner.count(OPEN) == 1
    idx = base + inner.index(OPEN); SEP = sep10(raw)
    return (idx, idx + len(OPEN), OPEN + SEP.join(stmts) + SEP)
def op_append_last(base, inner, stmts, raw):             # insert as last statement(s) of a process
    SEP = sep10(raw); CLOSE = SEP[:-2] + '</statements>'  # </statements> sits at 8 spaces vs 10
    assert inner.count(CLOSE) == 1
    idx = base + inner.index(CLOSE); return (idx, idx + len(CLOSE), SEP + SEP.join(stmts) + CLOSE)

# ---------- add signal behaviors to a node (depth-1 safe) ----------
def _mask_progdata(seg): return PROG.sub(lambda m: 'ProgramData "' + 'X' * len(m.group(1)) + '"', seg)
def last_top_func_pos(seg):
    """pos (in seg) of the last `Functionality "` that is at node-body depth 1.
    NOT the file-last one: some nodes have an rPhysicsEntity Functionality at depth 0 (outside the
    node body) after PropertyHandling — inserting there puts the signal OUTSIDE the node."""
    masked = _mask_progdata(seg); fps = [m.start() for m in re.finditer(r'Functionality "', masked)]
    d = 0; instr = False; depth = {}; fset = set(fps)
    for i, ch in enumerate(masked):
        if i in fset: depth[i] = d
        if ch == '"': instr = not instr
        elif not instr:
            if ch == '{': d += 1
            elif ch == '}': d -= 1
    top = [p for p in fps if depth[p] == 1]; assert top, "no depth-1 Functionality"; return top[-1]
def sig_block(sid, name, autoreset=1):
    return '\n'.join(['Functionality "rSimBoolSignal"', '{', 'Id %d' % sid, 'Name "%s"' % name,
                      'Visible 1', 'Connections', '{', '}', 'AutomaticReset %d' % autoreset, '}'])
def op_add_signals(raw, marks, i, names, autoreset=1):
    """op to add rSimBoolSignal behaviors `names` to node index i. Id = node-span max+1.."""
    ns, ne = node_span(raw, marks, i); seg = raw[ns:ne]
    base_id = max(int(x) for x in re.findall(r'\nId (\d+)', seg))
    ins = ns + last_top_func_pos(seg)
    blk = "".join(sig_block(base_id + 1 + k, n, autoreset) + '\n' for k, n in enumerate(names))
    return (ins, ins, blk)

# ---------- validation ----------
def validate_xml(raw):
    """[] if every ProcessExecutor's escaped XML parses; else [(proc, err)...]."""
    errs = []
    for pm in PROG.finditer(raw):
        s = pm.group(1)
        if "<process name=" not in s: continue
        dec = s.replace("\\r", "").replace("\\n", "\n").replace('\\"', '"')
        try: minidom.parseString(dec)
        except Exception as e:
            pn = PROC.search(s); errs.append((pn.group(1) if pn else "?", str(e)))
    return errs
def braces_balanced(raw): return raw.count('{') == raw.count('}')
def node_signals_depth1(raw):
    """node name -> set(signal names) that are real behaviors (brace depth 1 inside node body)."""
    marks = node_marks(raw); out = {}
    for i, (pos, name) in enumerate(marks):
        ns, ne = node_span(raw, marks, i); seg = raw[ns:ne]; masked = _mask_progdata(seg); found = set()
        for mm in re.finditer(r'Functionality "rSimBoolSignal"\n\{\nId \d+\nName "([^"]*)"', masked):
            pre = masked[:mm.start()]; d = 0; ins = False
            for ch in pre:
                if ch == '"': ins = not ins
                elif not ins:
                    if ch == '{': d += 1
                    elif ch == '}': d -= 1
            if d == 1: found.add(mm.group(1))
        out[name] = found
    return out
def unresolved_signal_refs(raw, only_prefix=None):
    """[(proc, component, signal)] where a Wait/Send references a signal not present (depth-1) on that node.
    Catches VC 'No signal selected'. only_prefix e.g. 'step' to check just baton signals."""
    avail = node_signals_depth1(raw); bad = []
    sig_re = re.compile(r'<statement type=\\"(I(?:Wait|Send)SignalStatement)\\">(.*?)</statement>', re.DOTALL)
    for pm in PROG.finditer(raw):
        s = pm.group(1); pn = PROC.search(s)
        if not pn: continue
        for m in sig_re.finditer(s):
            b = m.group(2); c = prop(b, "Component"); sg = prop(b, "Signal")
            if not sg or (only_prefix and not sg.startswith(only_prefix)): continue
            if c not in avail or sg not in avail[c]: bad.append((pn.group(1), c, sg))
    return bad
def dangling_signal_stmts(raw):
    """count of Wait/Send statements whose Signal property has no value= (broken ref -> 'No signal selected')."""
    return len(re.findall(re.escape('name=' + Q + 'Signal' + Q) + r'\s+isvisible=', raw))

# ---------- inspection helpers ----------
KEY = {"ITransportInStatement": "IN", "IStartTransportInStatement": "IN*", "IWaitTransportStatement": "WT",
       "IWorkStatement": "WORK", "ITransportOutStatement": "OUT", "IProcessDelayStatement": "DLY",
       "ICreateProductStatement": "CREATE", "IWaitSignalStatement": "WAIT", "ISendSignalStatement": "SEND",
       "IChangeProductTypeStatement": "CHG", "IAttachProductStatement": "ATTACH", "ISwitchCaseStatement": "SWITCH",
       "IWhileStatement": "WHILE", "IRemoveProductStatement": "RM"}
def stmt_sequence(inner):
    """compact list of statement tags for a process inner; signals annotated with (signal@component)."""
    out = []
    for m in STMT.finditer(inner):
        t = m.group(1)
        if t in ("IWaitSignalStatement", "ISendSignalStatement"):
            out.append("%s(%s@%s)" % (KEY[t], prop(m.group(2), "Signal") or "-", prop(m.group(2), "Component")))
        else:
            out.append(KEY.get(t, t))
    return out
def print_messages(inner):
    """[(message)] of IProcessPrintStatement (raw &quot;-escaped)."""
    out = []
    for m in STMT.finditer(inner):
        if m.group(1) == "IProcessPrintStatement":
            mm = re.search(r'name=\\"Message\\" value=\\"(.*?)\\" isvisible', m.group(0))
            if mm: out.append(mm.group(1))
    return out
def work_controllers(inner):
    return [prop(m.group(2), "Controller") for m in STMT.finditer(inner) if m.group(1) == "IWorkStatement" and prop(m.group(2), "Controller")]

# ---------- product type GUID -> name (from ProcessFlowGroups / creators) ----------
def product_flow_groups(raw):
    """flow-group name -> [product GUIDs]. (Product type names are not stored plainly;
    map GUIDs to a group / to the process that accepts them via TransportIn product filters.)"""
    out = {}
    blk = re.search(r'ProcessFlowGroups(.*?)\n    \}\n', raw, re.DOTALL)
    seg = blk.group(1) if blk else raw
    for g in re.finditer(r'ProcessFlowGroup\s*\{\s*Name "([^"]*)"(.*?)\}\s*\}', seg, re.DOTALL):
        out[g.group(1)] = re.findall(r'ProductType "([0-9a-f-]{36})"', g.group(2))
    return out

# ---------- .vcmx packaging (see SKILL.md rules) ----------
def backup_then_path(vcmx):
    if os.path.exists(vcmx):
        base = vcmx[:-5]
        idxs = [int(m.group(1)) for f in glob.glob(base + ".bk*") for m in [re.search(r"\.bk(\d+)$", f)] if m]
        os.rename(vcmx, "%s.bk%d" % (base, max(idxs, default=0) + 1))
    return vcmx
def folder_stale(vcmx, folder):
    if not os.path.isdir(folder): return True
    newest = max((os.path.getmtime(os.path.join(r, f)) for r, _, fs in os.walk(folder) for f in fs), default=0)
    return os.path.exists(vcmx) and os.path.getmtime(vcmx) > newest
def extract_vcmx(vcmx, folder):
    os.makedirs(folder, exist_ok=True)
    with zipfile.ZipFile(vcmx) as z: z.extractall(folder)
def zip_vcmx(folder, out_vcmx):
    out_vcmx = backup_then_path(out_vcmx)
    if os.path.exists(out_vcmx): os.remove(out_vcmx)
    with zipfile.ZipFile(out_vcmx, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(folder):
            for f in sorted(fs):
                if "bk" in f.lower() or f.endswith(".vcmx"): continue
                full = os.path.join(r, f); z.write(full, os.path.relpath(full, folder))
    with zipfile.ZipFile(out_vcmx) as z:
        assert "layout.rsc" in z.namelist(), "layout.rsc must be at zip root"
    return out_vcmx
