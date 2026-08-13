import os
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# =======================
#   CONFIGURAÇÃO
# =======================
USER_LOG_FOLDER = r"\\LocalizaçãoDosLogs"
MACHINE_LOG_FOLDER = r"\\LocalizaçãoDosLogs"

# =======================
#   ESTADO GLOBAL
# =======================
current_results = []
search_thread = None
stop_event = threading.Event()

# =======================
#   REGEX + PARSERS
# =======================

# Aceitar datas com "/" ou "-"
DATE_RE = r'(?P<date>\d{2}[/-]\d{2}[/-]\d{4})'
TIME_RE = r'(?P<time>\d{1,2}:\d{2}:\d{2}(?:[,\.]\d{1,6})?)'

# Formatos de LOG de COMPUTADOR:
# Ex.: "Log In: teste ON pc-teste12 with IP xxx.xxx.xxx.xxx at 14/05/2025  7:35:28,92"

RE_LOGIN_COMP = re.compile(
    rf'^Log\s*In:?\s+(?P<user>\S+)\s+ON\s+(?P<pc>\S+)\s+with\s+IP\s+(?P<ip>\S+)\s+at\s+{DATE_RE}\s+{TIME_RE}\s*$',
    re.IGNORECASE
)
RE_LOGOFF_COMP = re.compile(
    rf'^Log\s*Off\s+(?P<user>\S+)\s+{DATE_RE}\s+{TIME_RE}\s*$',
    re.IGNORECASE
)

# Formatos de LOG de UTILIZADOR:
# Ex.: "Log In: 
# Ex.: "Log Off 
RE_LOGIN_USER = re.compile(
    rf'^Log\s*In:?\s+(?P<pc>\S+)\s+(?P<ip>\S+)\s+{DATE_RE}\s+{TIME_RE}\s*$',
    re.IGNORECASE
)
RE_LOGOFF_USER = re.compile(
    rf'^Log\s*Off\s+(?P<pc>\S+)\s+{DATE_RE}\s+{TIME_RE}\s*$',
    re.IGNORECASE
)

def _parse_datetime(date_str: str, time_str: str):
    """
    Aceita data com "/" ou "-" e hora com "," ou "." e frações 1-6 dígitos.
    Normaliza para datetime (microsegundos).
    """
    try:
        date_str = date_str.strip().replace('-', '/')

        t = time_str.replace('.', ',').strip()
        if ',' in t:
            hhmmss, frac = t.split(',', 1)
            frac = re.sub(r'\D', '', frac)[:6].ljust(6, '0')
            t_norm = f"{hhmmss},{frac}"
        else:
            t_norm = f"{t},000000"

        return datetime.strptime(f"{date_str} {t_norm}", "%d/%m/%Y %H:%M:%S,%f")
    except Exception:
        return None

def parse_log_line(line: str, default_user: str = "", default_pc: str = ""):
    """
    Devolve dict: User, PC, IP, Tipo, DataHora, DataHoraObj
    """
    s = line.strip()
    if not s:
        return None

    # 1) Formatos de computador
    m = RE_LOGIN_COMP.match(s)
    if m:
        user = m.group('user') or default_user
        pc   = m.group('pc') or default_pc
        ip   = m.group('ip') or ''
        date = m.group('date'); time = m.group('time')
        dt   = _parse_datetime(date, time)
        return {'User': user, 'PC': pc, 'IP': ip, 'Tipo': 'Log In',
                'DataHora': f"{date} {time}", 'DataHoraObj': dt}

    m = RE_LOGOFF_COMP.match(s)
    if m:
        user = m.group('user') or default_user
        pc   = default_pc
        ip   = ''
        date = m.group('date'); time = m.group('time')
        dt   = _parse_datetime(date, time)
        return {'User': user, 'PC': pc, 'IP': ip, 'Tipo': 'Log Off',
                'DataHora': f"{date} {time}", 'DataHoraObj': dt}

    # 2) Formatos de utilizador
    m = RE_LOGIN_USER.match(s)
    if m:
        user = default_user
        pc   = m.group('pc') or default_pc
        ip   = m.group('ip') or ''
        date = m.group('date'); time = m.group('time')
        dt   = _parse_datetime(date, time)
        return {'User': user, 'PC': pc, 'IP': ip, 'Tipo': 'Log In',
                'DataHora': f"{date} {time}", 'DataHoraObj': dt}

    m = RE_LOGOFF_USER.match(s)
    if m:
        user = default_user
        pc   = m.group('pc') or default_pc
        ip   = ''
        date = m.group('date'); time = m.group('time')
        dt   = _parse_datetime(date, time)
        return {'User': user, 'PC': pc, 'IP': ip, 'Tipo': 'Log Off',
                'DataHora': f"{date} {time}", 'DataHoraObj': dt}

    return None

# =======================
#   PESQUISAS (THREAD)
# =======================

def search_user_file(user: str):
    """
    Pesquisa em \\User\\<user>.log e garante PC nos 'Log Off' usando último PC de 'Log In'.
    """
    results = []
    file_path = os.path.join(USER_LOG_FOLDER, f"{user}.log")
    if not os.path.exists(file_path):
        return results

    last_pc = ""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if stop_event.is_set():
                break

            row = parse_log_line(line, default_user=user, default_pc=last_pc)
            if not row:
                continue

            row['User'] = user

            if row['Tipo'] == 'Log In' and row.get('PC'):
                last_pc = row['PC']

            if row['Tipo'] == 'Log Off' and not row.get('PC') and last_pc:
                row['PC'] = last_pc

            results.append(row)

    return results

def search_machine_fast(machine: str):
    """
    Procura rápida no ficheiro \\Computer\\<machine>.log.
    """
    file_path = os.path.join(MACHINE_LOG_FOLDER, f"{machine}.log")
    if not os.path.exists(file_path):
        return None

    results = []
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if stop_event.is_set():
                break
            row = parse_log_line(line, default_pc=machine)
            if row:
                results.append(row)
    return results

def search_machine_fallback_scan(machine: str):
    """
    Fallback: varre todos os ficheiros em \\User à procura de linhas que contenham a máquina (case-insensitive).
    """
    results = []
    entries = [e for e in os.scandir(USER_LOG_FOLDER) if e.is_file() and e.name.lower().endswith('.log')]
    total = len(entries)
    scanned = 0

    machine_l = machine.lower()

    for entry in entries:
        if stop_event.is_set():
            break

        user = os.path.splitext(entry.name)[0]
        try:
            with open(entry.path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if stop_event.is_set():
                        break

                    if machine_l in line.lower():
                        row = parse_log_line(line, default_user=user, default_pc=machine)
                        if row:
                            row['User'] = user
                            results.append(row)
        except Exception:
            pass

        scanned += 1
        if scanned % 20 == 0 or scanned == total:
            update_status_async(f"A procurar (fallback): {scanned}/{total} ficheiros...")

    return results

# =======================
#   HELPERS: ORDENAR + LIMITAR
# =======================

def get_ordered_results(results_list):
    reverse_order = sort_var.get()  # True = mais recente primeiro
    return sorted(
        results_list,
        key=lambda x: x['DataHoraObj'] if x['DataHoraObj'] else datetime.min,
        reverse=reverse_order
    )

def apply_limit(ordered_list):
    """
    Se ativado, limita a N primeiros da ordem atual (ex.: com 'Mais recente primeiro' => últimos N).
    """
    if not limit_var.get():
        return ordered_list

    try:
        n = int(limit_n_var.get())
    except Exception:
        n = 5

    if n <= 0:
        return ordered_list

    return ordered_list[:n]

# =======================
#   UPDATE UI (THREAD-SAFE)
# =======================

def update_status_async(text):
    root.after(0, lambda: status_var.set(text))

def show_results_async(results_list):
    def _fill():
        ordered = get_ordered_results(results_list)
        view_list = apply_limit(ordered)

        for row_id in tree.get_children():
            tree.delete(row_id)

        for r in view_list:
            tree.insert('', 'end', values=(r['User'], r['PC'], r['IP'], r['Tipo'], r['DataHora']))

        reverse_order = sort_var.get()
        limit_txt = ""
        if limit_var.get():
            limit_txt = f" | Limitado a: {len(view_list)}"

        status_var.set(
            f"Resultados: {len(ordered)}{limit_txt} | Ordem: {'Mais recente' if reverse_order else 'Mais antigo'} primeiro"
        )

        set_busy(False)
        btn_export.config(state='normal' if view_list else 'disabled')

    root.after(0, _fill)

def show_error_async(msg):
    root.after(0, lambda: (set_busy(False), messagebox.showerror("Erro", msg)))

# =======================
#   THREAD RUNNER
# =======================

def run_search(target, search_type):
    try:
        if search_type == 'user':
            update_status_async("A procurar: ficheiro do utilizador...")
            results = search_user_file(target)
        else:
            update_status_async("A procurar: ficheiro da máquina (rápido)...")
            fast = search_machine_fast(target)

            if stop_event.is_set():
                return

            if fast is not None:
                results = fast
            else:
                update_status_async("Ficheiro da máquina não encontrado. A fazer varrimento por utilizadores...")
                results = search_machine_fallback_scan(target)

        if stop_event.is_set():
            update_status_async("Pesquisa cancelada.")
            set_busy(False)
            return

        def _save_and_show():
            global current_results
            current_results = results
            show_results_async(current_results)

        root.after(0, _save_and_show)

    except Exception as e:
        show_error_async(f"Erro ao pesquisar logs: {e}")

# =======================
#   CONTROLO BUSY/IDLE
# =======================

def set_busy(is_busy):
    if is_busy:
        btn_search.config(state='disabled')
        btn_export.config(state='disabled')
        btn_cancel.config(state='normal')
        progress.grid(row=0, column=4, padx=10)
        progress.start(12)
    else:
        btn_search.config(state='normal')
        btn_cancel.config(state='disabled')
        progress.stop()
        progress.grid_remove()

# =======================
#   VALIDAÇÃO INPUT
# =======================

def validate_target(target: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", target))

# =======================
#   AÇÕES UI
# =======================

def on_search():
    target = entry.get().strip()
    if not target:
        messagebox.showwarning("Aviso", "Digite um usuário ou máquina para pesquisar.")
        entry.focus_set()
        return

    if not validate_target(target):
        messagebox.showwarning("Aviso", "Formato inválido. Use apenas letras, números, - _ .")
        entry.focus_set()
        return

    search_type = var.get()

    global search_thread
    if search_thread and search_thread.is_alive():
        messagebox.showinfo("Aviso", "Já existe uma pesquisa em curso. Cancele antes de iniciar outra.")
        return

    stop_event.clear()
    set_busy(True)
    status_var.set("A iniciar pesquisa...")

    search_thread = threading.Thread(target=run_search, args=(target, search_type), daemon=True)
    search_thread.start()

def on_cancel():
    if search_thread and search_thread.is_alive():
        stop_event.set()
        status_var.set("A interromper pesquisa...")

def on_export():
    if not current_results:
        messagebox.showwarning("Aviso", "Não há resultados para exportar.")
        return

    file_path = filedialog.asksaveasfilename(
        defaultextension='.csv',
        filetypes=[('CSV Files', '*.csv')],
        title="Guardar resultados como"
    )
    if file_path:
        import csv
        keys = ['User', 'PC', 'IP', 'Tipo', 'DataHora']

        ordered = get_ordered_results(current_results)
        export_list = apply_limit(ordered)  # exporta o que estás a ver (limitado se ativo)

        with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, keys, delimiter=';')
            writer.writeheader()
            writer.writerows(export_list)

        messagebox.showinfo("Sucesso", f"Resultados exportados para {file_path}")

def toggle_sort():
    show_results_async(current_results)

def toggle_limit():
    # Reaplica limite na lista atual
    show_results_async(current_results)

def handle_enter(event=None):
    if str(btn_search['state']) == 'disabled':
        return "break"
    on_search()
    return "break"

def on_close():
    if search_thread and search_thread.is_alive():
        stop_event.set()
    root.destroy()

# =======================
#   GUI
# =======================

root = tk.Tk()
root.title("Pesquisa de Logs AD")
root.geometry("1000x620")
root.minsize(820, 520)
root.protocol("WM_DELETE_WINDOW", on_close)

style = ttk.Style(root)
if 'vista' in style.theme_names():
    style.theme_use('vista')
elif 'clam' in style.theme_names():
    style.theme_use('clam')

root.columnconfigure(0, weight=1)
root.rowconfigure(2, weight=1)

# Header
header = ttk.Frame(root, padding=(10, 10))
header.grid(row=0, column=0, sticky="ew")
ttk.Label(header, text="Pesquisa de Logs AD", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
status_var = tk.StringVar(value="Pronto")
ttk.Label(header, textvariable=status_var, font=("Segoe UI", 10)).grid(row=0, column=1, sticky="e")
header.columnconfigure(0, weight=1)
header.columnconfigure(1, weight=1)

# Controles
controls = ttk.Frame(root, padding=(10, 5))
controls.grid(row=1, column=0, sticky="ew")
controls.columnconfigure(1, weight=1)

# Pesquisa
search_frame = ttk.LabelFrame(controls, text="Pesquisa", padding=(10, 10))
search_frame.grid(row=0, column=0, columnspan=4, sticky="ew")
search_frame.columnconfigure(1, weight=1)

ttk.Label(search_frame, text="Alvo (user ou máquina):").grid(row=0, column=0, padx=(0, 8), sticky="w")
entry = ttk.Entry(search_frame)
entry.grid(row=0, column=1, padx=(0, 8), sticky="ew")
entry.focus_set()

# Opções
options_frame = ttk.LabelFrame(controls, text="Opções", padding=(10, 8))
options_frame.grid(row=1, column=0, sticky="w", padx=(0, 5), pady=(6, 0))

var = tk.StringVar(value='user')
ttk.Radiobutton(options_frame, text='User', variable=var, value='user').grid(row=0, column=0, padx=5, pady=2, sticky="w")
ttk.Radiobutton(options_frame, text='Máquina', variable=var, value='machine').grid(row=0, column=1, padx=5, pady=2, sticky="w")

sort_var = tk.BooleanVar(value=True)  # True = mais recente primeiro
ttk.Checkbutton(options_frame, text="Mais recente primeiro", variable=sort_var, command=toggle_sort).grid(
    row=0, column=2, padx=10, pady=2, sticky="w"
)

# ---- NOVO: LIMITE ÚLTIMAS N LINHAS ----
limit_var = tk.BooleanVar(value=True)        # ON por defeito
limit_n_var = tk.IntVar(value=5)             # 5 por defeito

ttk.Checkbutton(options_frame, text="Limitar a", variable=limit_var, command=toggle_limit).grid(
    row=0, column=3, padx=(10, 2), pady=2, sticky="w"
)

spin = ttk.Spinbox(options_frame, from_=1, to=500, width=5, textvariable=limit_n_var, command=toggle_limit)
spin.grid(row=0, column=4, padx=(2, 4), pady=2, sticky="w")
ttk.Label(options_frame, text="linhas").grid(row=0, column=5, padx=(0, 6), pady=2, sticky="w")

# Ações
actions_frame = ttk.Frame(controls)
actions_frame.grid(row=1, column=1, sticky="e", pady=(6, 0))

btn_search = ttk.Button(actions_frame, text='Pesquisar', command=on_search)
btn_search.grid(row=0, column=0, padx=5)

btn_export = ttk.Button(actions_frame, text='Exportar CSV', command=on_export, state='disabled')
btn_export.grid(row=0, column=1, padx=5)

btn_cancel = ttk.Button(actions_frame, text='Cancelar', command=on_cancel, state='disabled')
btn_cancel.grid(row=0, column=2, padx=5)

progress = ttk.Progressbar(actions_frame, mode='indeterminate', length=180)
progress.grid_remove()

# Tabela
table_frame = ttk.Frame(root, padding=(10, 10))
table_frame.grid(row=2, column=0, sticky="nsew")
table_frame.columnconfigure(0, weight=1)
table_frame.rowconfigure(0, weight=1)

columns = ('User', 'PC', 'IP', 'Tipo', 'DataHora')
tree = ttk.Treeview(table_frame, columns=columns, show='headings')
tree.grid(row=0, column=0, sticky="nsew")

tree.heading('User', text='User')
tree.heading('PC', text='PC')
tree.heading('IP', text='IP')
tree.heading('Tipo', text='Tipo')
tree.heading('DataHora', text='Data/Hora')

tree.column('User', width=180, anchor='w')
tree.column('PC', width=160, anchor='w')
tree.column('IP', width=160, anchor='w')
tree.column('Tipo', width=110, anchor='center')
tree.column('DataHora', width=220, anchor='center')

scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=tree.yview)
scrollbar.grid(row=0, column=1, sticky='ns')
tree.configure(yscrollcommand=scrollbar.set)

style.configure("Treeview", rowheight=24)

# BINDS DO ENTER
entry.bind('<Return>', handle_enter)
root.bind('<Return>', handle_enter)
root.bind('<KP_Enter>', handle_enter)

root.mainloop()
