#!/usr/bin/env python3
"""
Controle de Brilho — ajusta o brilho de monitores externos via DDC/CI
e oferece atalhos de teclado globais para níveis pré-definidos.

Uso:
    python brilho.py                  # ícone na bandeja + janela + atalhos
    python brilho.py --minimizado     # só o ícone na bandeja (usado ao iniciar o Windows)
    python brilho.py --sem-janela     # só os atalhos, sem janela nem bandeja
    python brilho.py --listar         # lista monitores e brilho atual
    python brilho.py --definir 50     # 50% em todos os monitores
    python brilho.py --definir 40 70  # 40% no monitor 1, 70% no monitor 2
    python brilho.py --preset Noite   # aplica um preset do config.json
    python brilho.py --simular        # testa sem monitores reais
"""

import argparse
import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

if getattr(sys, "frozen", False):  # empacotado como .exe (PyInstaller)
    PASTA = Path(sys.executable).resolve().parent
else:
    PASTA = Path(__file__).resolve().parent


def _pasta_gravavel(pasta):
    """Testa de verdade se dá para criar arquivos na pasta (ex.: Arquivos de
    Programas exige administrador e recusa a gravação)."""
    try:
        with tempfile.TemporaryFile(dir=pasta):
            return True
    except OSError:
        return False


def _escolher_arquivo_config():
    """Onde fica o config.json:
    1. ao lado do programa, se já existir um ali e a pasta aceitar gravação
       (modo portátil e compatibilidade com versões anteriores);
    2. senão, na pasta do usuário: %APPDATA%\\ControleBrilho (Windows) ou
       ~/.config/controle-brilho (Linux).
    """
    local = PASTA / "config.json"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        do_usuario = base / "ControleBrilho" / "config.json"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        do_usuario = base / "controle-brilho" / "config.json"

    if local.exists() and _pasta_gravavel(PASTA):
        return local
    if do_usuario.exists():
        return do_usuario
    if local.exists():
        # Existe ao lado do programa, mas a pasta é protegida: copia para o usuário.
        try:
            do_usuario.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, do_usuario)
        except OSError:
            return local  # ao menos dá para ler
        return do_usuario
    if not getattr(sys, "frozen", False) and _pasta_gravavel(PASTA):
        return local  # rodando pelo código-fonte: mantém ao lado do script
    return do_usuario


ARQUIVO_CONFIG = _escolher_arquivo_config()
TEM_CONSOLE = sys.stdout is not None and not getattr(sys, "frozen", False)


def avisar(mensagem, erro=False):
    """Mostra no terminal ou, no .exe sem console, numa caixa de diálogo."""
    if TEM_CONSOLE:
        print(mensagem, file=sys.stderr if erro else sys.stdout)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        (messagebox.showerror if erro else messagebox.showinfo)("Controle de Brilho", mensagem)
        r.destroy()
    except Exception:
        pass


def falhar(mensagem):
    avisar(mensagem, erro=True)
    sys.exit(1)

CONFIG_PADRAO = {
    "iniciar_com_windows": True,
    "passo": 10,
    "nomes_monitores": ["Esquerda", "Direita"],
    "atalhos": {
        "aumentar": "<ctrl>+<alt>+<page_up>",
        "diminuir": "<ctrl>+<alt>+<page_down>",
    },
    "presets": [
        {"nome": "Noite", "atalho": "<ctrl>+<alt>+1", "brilho": 10},
        {"nome": "Médio", "atalho": "<ctrl>+<alt>+2", "brilho": 50},
        {"nome": "Dia", "atalho": "<ctrl>+<alt>+3", "brilho": 100},
        {"nome": "Leitura", "atalho": "<ctrl>+<alt>+4", "brilho": [40, 60]},
    ],
}


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

def carregar_config():
    if not ARQUIVO_CONFIG.exists():
        try:
            ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            ARQUIVO_CONFIG.write_text(
                json.dumps(CONFIG_PADRAO, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if TEM_CONSOLE:
                print(f"Arquivo de configuração criado em {ARQUIVO_CONFIG}")
        except OSError as e:
            avisar(f"Não foi possível criar {ARQUIVO_CONFIG}:\n{e}\n\n"
                   "O programa vai usar as configurações padrão.", erro=True)
            return json.loads(json.dumps(CONFIG_PADRAO))
    try:
        cfg = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        falhar(f"Erro no {ARQUIVO_CONFIG.name}: {e}")
    for chave, valor in CONFIG_PADRAO.items():
        cfg.setdefault(chave, valor)
    return cfg


def salvar_config(cfg):
    try:
        ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        avisar(f"Não foi possível salvar o config.json: {e}", erro=True)
        return False


def salvar_opcao(chave, valor):
    """Altera uma única opção no config.json, preservando o resto do arquivo."""
    try:
        dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
        dados[chave] = valor
        ARQUIVO_CONFIG.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        avisar(f"Não foi possível salvar o config.json: {e}", erro=True)


# --------------------------------------------------------------------------
# Monitores
# --------------------------------------------------------------------------

class MonitorDDC:
    """Monitor físico controlado via DDC/CI (biblioteca monitorcontrol)."""

    def __init__(self, monitor, nome):
        self._mon = monitor
        self.nome = nome

    def ler(self):
        with self._mon:
            return int(self._mon.get_luminance())

    def definir(self, valor):
        with self._mon:
            self._mon.set_luminance(int(valor))


class MonitorSimulado:
    """Monitor falso, para testar a interface e os atalhos."""

    def __init__(self, nome, valor=70):
        self.nome = nome
        self._valor = valor

    def ler(self):
        return self._valor

    def definir(self, valor):
        time.sleep(0.05)  # simula a latência do DDC/CI
        self._valor = int(valor)
        print(f"[simulado] {self.nome} -> {valor}%")


def detectar_monitores(cfg, simular=False, tentativas=1):
    nomes = cfg.get("nomes_monitores") or []

    def nome(i):
        return nomes[i] if i < len(nomes) else f"Monitor {i + 1}"

    if simular:
        return [MonitorSimulado(nome(0), 70), MonitorSimulado(nome(1), 40)]

    try:
        from monitorcontrol import get_monitors
    except ImportError:
        falhar("Instale as dependências: pip install -r requirements.txt")

    erro = None
    for tentativa in range(max(1, tentativas)):
        if tentativa:
            time.sleep(5)
        try:
            lista = get_monitors()
        except Exception as e:
            erro = e
            continue
        encontrados = []
        for mon in lista:
            candidato = MonitorDDC(mon, nome(len(encontrados)))
            try:
                candidato.ler()  # telas sem DDC/CI (ex.: a do notebook) falham aqui
            except Exception:
                continue
            encontrados.append(candidato)
        if encontrados:
            return encontrados

    if erro:
        falhar(
            "Não foi possível consultar os monitores via DDC/CI.\n\n"
            f"Detalhe: {erro}\n\n"
            "Verifique se o DDC/CI está ativado no menu (OSD) de cada monitor "
            "e se o driver da placa de vídeo está atualizado."
        )
    falhar(
        "Nenhum monitor com DDC/CI encontrado.\n"
        "- Verifique se o DDC/CI está ativado no menu (OSD) de cada monitor.\n"
        "- No Linux: 'sudo modprobe i2c-dev' e acesso a /dev/i2c-* (veja o README)."
    )


# --------------------------------------------------------------------------
# Controlador: guarda o brilho desejado e aplica em uma thread separada
# --------------------------------------------------------------------------

class Controlador:
    """
    DDC/CI é lento (dezenas a centenas de ms por comando). Para a interface
    não travar, os pedidos vão para uma fila; se chegarem vários pedidos para
    o mesmo monitor antes de serem aplicados, só o último é enviado.
    """

    def __init__(self, monitores, passo=10):
        self.monitores = monitores
        self.passo = passo
        self.valores = []
        for m in monitores:
            try:
                self.valores.append(m.ler())
            except Exception:
                self.valores.append(50)
        self._pendentes = {}
        self._cond = threading.Condition()
        threading.Thread(target=self._trabalhador, daemon=True).start()

    def definir(self, indice, valor):
        valor = max(0, min(100, int(round(valor))))
        with self._cond:
            self.valores[indice] = valor
            self._pendentes[indice] = valor
            self._cond.notify()

    def aplicar(self, brilho):
        """brilho = número (todos os monitores) ou lista (um valor por monitor)."""
        if isinstance(brilho, (int, float)):
            brilho = [brilho] * len(self.monitores)
        for i, valor in enumerate(brilho[: len(self.monitores)]):
            if valor is not None:
                self.definir(i, valor)

    def ajustar(self, sinal):
        for i, atual in enumerate(list(self.valores)):
            self.definir(i, atual + sinal * self.passo)

    def _trabalhador(self):
        while True:
            with self._cond:
                while not self._pendentes:
                    self._cond.wait()
                indice, valor = self._pendentes.popitem()
            try:
                self.monitores[indice].definir(valor)
            except Exception as e:
                print(f"Falha ao ajustar {self.monitores[indice].nome}: {e}")


# --------------------------------------------------------------------------
# Atalhos globais
# --------------------------------------------------------------------------

def _vk_de_caractere(c):
    """Código virtual (Windows) da tecla que produz o caractere `c`."""
    if c.isascii() and c.isalnum():
        return ord(c.upper())
    try:
        import ctypes
        r = ctypes.windll.user32.VkKeyScanW(ord(c))
        return r & 0xFF if r != -1 else None
    except Exception:
        return None


class GerenciadorAtalhos:
    """Escuta o teclado globalmente e dispara presets e ajustes.

    Pode ser recarregado (após editar as configurações) e pausado (enquanto
    o usuário grava um novo atalho na tela de configurações).
    """

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.pausado = False
        self._combinacoes = []
        self._ouvinte = None
        self._keyboard = None
        try:
            from pynput import keyboard
            self._keyboard = keyboard
        except Exception as e:
            avisar(f"Atalhos globais indisponíveis: {e}", erro=True)

    @property
    def ativo(self):
        return self._ouvinte is not None and bool(self._combinacoes)

    def _normalizar(self, tecla):
        # No Windows, com Ctrl/Alt pressionados a tecla "1" chega só como código
        # virtual (vk=49), sem o caractere. Por isso comparamos tudo pelo vk.
        kb = self._keyboard
        if not isinstance(tecla, kb.KeyCode) or sys.platform != "win32":
            return tecla
        if tecla.vk is not None:
            return kb.KeyCode.from_vk(tecla.vk)
        if tecla.char and len(tecla.char) == 1:
            vk = _vk_de_caractere(tecla.char)
            if vk:
                return kb.KeyCode.from_vk(vk)
        return tecla

    def carregar(self, cfg, mostrar_erros=True):
        """(Re)lê os atalhos do cfg. Retorna a lista de problemas encontrados."""
        if not self._keyboard:
            return []
        kb = self._keyboard
        mapa, problemas = {}, []

        def adicionar(atalho, acao, descricao):
            if not atalho:
                return
            try:
                teclas = kb.HotKey.parse(atalho)
            except ValueError:
                problemas.append(f"Atalho inválido ignorado: {atalho!r} ({descricao})")
                return
            chave = frozenset(self._normalizar(t) for t in teclas)
            if chave in mapa:
                problemas.append(f"Atalho repetido ignorado: {descrever_atalho(atalho)} ({descricao})")
                return
            mapa[chave] = acao

        ctrl = self.ctrl
        atalhos = cfg.get("atalhos", {})
        adicionar(atalhos.get("aumentar"), lambda: ctrl.ajustar(+1), "aumentar")
        adicionar(atalhos.get("diminuir"), lambda: ctrl.ajustar(-1), "diminuir")
        for p in cfg.get("presets", []):
            adicionar(p.get("atalho"), lambda b=p.get("brilho"): ctrl.aplicar(b), p.get("nome"))

        self._combinacoes = [kb.HotKey(list(teclas), acao) for teclas, acao in mapa.items()]
        if self._combinacoes and self._ouvinte is None:
            self._ouvinte = kb.Listener(on_press=self._ao_pressionar, on_release=self._ao_soltar)
            self._ouvinte.daemon = True
            self._ouvinte.start()
        if mostrar_erros and problemas:
            avisar("\n".join(problemas), erro=True)
        return problemas

    def _ao_pressionar(self, tecla):
        if self.pausado:
            return
        tecla = self._normalizar(self._ouvinte.canonical(tecla))
        for c in self._combinacoes:
            c.press(tecla)

    def _ao_soltar(self, tecla):
        tecla = self._normalizar(self._ouvinte.canonical(tecla))
        for c in self._combinacoes:
            c.release(tecla)

    def aguardar(self):
        if self._ouvinte:
            self._ouvinte.join()


NOMES_TECLAS = {
    "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win",
    "page_up": "PgUp", "page_down": "PgDn", "up": "Seta acima", "down": "Seta abaixo",
    "left": "Seta esquerda", "right": "Seta direita", "home": "Home", "end": "End",
    "insert": "Insert", "delete": "Delete", "space": "Espaço",
    "pause": "Pause", "print_screen": "PrtSc", "scroll_lock": "ScrLk",
}


def _nome_vk(vk):
    if 96 <= vk <= 105:
        return f"Num{vk - 96}"
    nomes = {106: "Num*", 107: "Num+", 109: "Num-", 110: "Num.", 111: "Num/"}
    if vk in nomes:
        return nomes[vk]
    try:
        import ctypes
        c = ctypes.windll.user32.MapVirtualKeyW(vk, 2)  # MAPVK_VK_TO_CHAR
        if c:
            return chr(c & 0xFFFF).upper()
    except Exception:
        pass
    return f"Tecla {vk}"


def descrever_atalho(atalho):
    """'<ctrl>+<alt>+<page_up>' -> 'Ctrl+Alt+PgUp'"""
    if not atalho:
        return ""
    partes = []
    for p in atalho.split("+"):
        nome = p[1:-1] if len(p) > 2 and p[0] == "<" and p[-1] == ">" else p
        if nome.isdigit() and len(p) > 1:
            partes.append(_nome_vk(int(nome)))
        else:
            partes.append(NOMES_TECLAS.get(nome, nome.upper()))
    return "+".join(partes)


# Teclas aceitas na gravação de atalhos (Windows: código virtual -> nome pynput)
_VK_NOMES = {0x21: "<page_up>", 0x22: "<page_down>", 0x23: "<end>", 0x24: "<home>",
             0x25: "<left>", 0x26: "<up>", 0x27: "<right>", 0x28: "<down>",
             0x2D: "<insert>", 0x2E: "<delete>", 0x20: "<space>", 0x13: "<pause>"}
_KEYSYM_NOMES = {"prior": "<page_up>", "next": "<page_down>", "end": "<end>",
                 "home": "<home>", "left": "<left>", "up": "<up>", "right": "<right>",
                 "down": "<down>", "insert": "<insert>", "delete": "<delete>",
                 "space": "<space>", "pause": "<pause>"}
MODIFICADORES_TK = {
    "control_l": "ctrl", "control_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
    "shift_l": "shift", "shift_r": "shift", "win_l": "cmd", "win_r": "cmd",
    "super_l": "cmd", "super_r": "cmd", "meta_l": "alt", "meta_r": "alt",
    "iso_level3_shift": "alt",
}


def tecla_de_evento_tk(evento):
    """Converte um <KeyPress> do tkinter no nome usado pelo pynput (ou None)."""
    if sys.platform == "win32":
        vk = evento.keycode
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if 0x70 <= vk <= 0x87:
            return f"<f{vk - 0x6F}>"
        if vk in _VK_NOMES:
            return _VK_NOMES[vk]
        if 0x60 <= vk <= 0x6F or 0xBA <= vk <= 0xC0 or 0xDB <= vk <= 0xDF or vk == 0xE2:
            return f"<{vk}>"  # teclado numérico e pontuação: pelo código virtual
        return None
    ks = evento.keysym.lower()
    if len(ks) == 1 and ks.isalnum():
        return ks
    if ks.startswith("f") and ks[1:].isdigit():
        return f"<{ks}>"
    return _KEYSYM_NOMES.get(ks)


# --------------------------------------------------------------------------
# Iniciar com o Windows (registro HKCU\...\Run — não precisa de administrador)
# --------------------------------------------------------------------------

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_RUN = "ControleBrilho"


def comando_autostart():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimizado'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpretador = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpretador}" "{Path(__file__).resolve()}" --minimizado'


def autostart_ativo():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            valor, _ = winreg.QueryValueEx(k, NOME_RUN)
            return bool(valor)
    except OSError:
        return False


def definir_autostart(ativo):
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
            if ativo:
                winreg.SetValueEx(k, NOME_RUN, 0, winreg.REG_SZ, comando_autostart())
            else:
                try:
                    winreg.DeleteValue(k, NOME_RUN)
                except FileNotFoundError:
                    pass
    except OSError as e:
        avisar(f"Não foi possível alterar o início automático: {e}", erro=True)


# --------------------------------------------------------------------------
# Instância única: se já estiver rodando, a 2ª cópia só pede para mostrar a janela
# --------------------------------------------------------------------------

PORTA_INSTANCIA = 47813


def reservar_instancia(pedidos, mostrar):
    """Retorna True se esta é a única instância; senão avisa a outra e retorna False."""
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        servidor.bind(("127.0.0.1", PORTA_INSTANCIA))
    except OSError:
        servidor.close()
        if mostrar:
            try:
                with socket.create_connection(("127.0.0.1", PORTA_INSTANCIA), timeout=2) as c:
                    c.sendall(b"mostrar")
            except OSError:
                pass
        return False

    servidor.listen(2)

    def atender():
        while True:
            try:
                conexao, _ = servidor.accept()
                with conexao:
                    if conexao.recv(32).startswith(b"mostrar"):
                        pedidos.put("mostrar")
            except OSError:
                return

    threading.Thread(target=atender, daemon=True).start()
    return True


# --------------------------------------------------------------------------
# Ícone na bandeja do sistema (pystray)
# --------------------------------------------------------------------------

def imagem_icone():
    from PIL import Image, ImageDraw
    arquivo = Path(getattr(sys, "_MEIPASS", PASTA)) / "icone.ico"
    if arquivo.exists():
        return Image.open(arquivo)
    import math
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cor = (255, 184, 28, 255)
    for k in range(8):
        a = k * math.pi / 4
        d.line([(32 + 21 * math.cos(a), 32 + 21 * math.sin(a)),
                (32 + 30 * math.cos(a), 32 + 30 * math.sin(a))], fill=cor, width=5)
    d.ellipse([17, 17, 47, 47], fill=cor)
    return img


def texto_dica(ctrl):
    partes = [f"{m.nome}: {v}%" for m, v in zip(ctrl.monitores, ctrl.valores)]
    return ("Brilho — " + " · ".join(partes))[:127]  # limite do Windows


def valores_do_preset(brilho, n):
    """Converte o campo 'brilho' de um preset numa lista com n valores (None = não altera)."""
    if isinstance(brilho, (int, float)):
        return [int(brilho)] * n
    lista = list(brilho or [])
    return (lista + [None] * n)[:n]


def centralizar(janela, referencia=None):
    """Posiciona a janela no centro da tela (ou sobre a janela de referência)."""
    janela.update_idletasks()
    w, h = janela.winfo_reqwidth(), janela.winfo_reqheight()
    if referencia is not None and referencia.winfo_viewable():
        x = referencia.winfo_rootx() + (referencia.winfo_width() - w) // 2
        y = referencia.winfo_rooty() + (referencia.winfo_height() - h) // 3
    else:
        x = (janela.winfo_screenwidth() - w) // 2
        y = (janela.winfo_screenheight() - h) // 3
    janela.geometry(f"+{max(0, x)}+{max(0, y)}")


def texto_valor(v):
    return "—" if v is None else f"{v}%"


# --------------------------------------------------------------------------
# Campo que grava um atalho: clique e pressione a combinação de teclas
# --------------------------------------------------------------------------

def criar_campo_atalho(pai, atalhos, valor_inicial=""):
    import tkinter as tk
    from tkinter import ttk

    quadro = ttk.Frame(pai)
    estado = {"valor": valor_inicial or "", "pressionadas": set(), "gravando": False}
    texto = tk.StringVar(value=descrever_atalho(estado["valor"]) or "(nenhum)")
    campo = ttk.Entry(quadro, textvariable=texto, width=22, state="readonly", cursor="hand2")
    campo.pack(side="left")

    def mostrar():
        texto.set(descrever_atalho(estado["valor"]) or "(nenhum)")

    def iniciar(_=None):
        estado["gravando"] = True
        estado["pressionadas"].clear()
        atalhos.pausado = True  # não dispara presets enquanto grava
        texto.set("Pressione a combinação…")

    def parar(_=None):
        estado["gravando"] = False
        atalhos.pausado = False
        mostrar()

    def modificadores(evento):
        mods = set(estado["pressionadas"])
        if evento.state & 0x4:
            mods.add("ctrl")
        if evento.state & 0x1:
            mods.add("shift")
        if evento.state & (0x20000 if sys.platform == "win32" else 0x8):
            mods.add("alt")
        return mods

    def ao_pressionar(evento):
        if not estado["gravando"]:
            iniciar()
        ks = evento.keysym.lower()
        if ks in MODIFICADORES_TK:
            estado["pressionadas"].add(MODIFICADORES_TK[ks])
            ordem = [m for m in ("ctrl", "alt", "shift", "cmd") if m in modificadores(evento)]
            texto.set("+".join(NOMES_TECLAS[m] for m in ordem) + "+…")
            return "break"
        if ks == "escape":
            quadro.winfo_toplevel().focus_set()
            return "break"
        tecla = tecla_de_evento_tk(evento)
        if tecla is None:
            texto.set("Tecla não suportada")
            return "break"
        mods = modificadores(evento)
        if not (mods & {"ctrl", "alt", "cmd"}) and not tecla.startswith("<f"):
            texto.set("Use Ctrl, Alt ou Win + tecla")
            return "break"
        ordem = [m for m in ("ctrl", "alt", "shift", "cmd") if m in mods]
        estado["valor"] = "+".join([f"<{m}>" for m in ordem] + [tecla])
        quadro.winfo_toplevel().focus_set()  # encerra a gravação
        return "break"

    def ao_soltar(evento):
        ks = evento.keysym.lower()
        if ks in MODIFICADORES_TK:
            estado["pressionadas"].discard(MODIFICADORES_TK[ks])
        return "break"

    def limpar():
        estado["valor"] = ""
        quadro.winfo_toplevel().focus_set()
        mostrar()

    campo.bind("<FocusIn>", iniciar)
    campo.bind("<FocusOut>", parar)
    campo.bind("<KeyPress>", ao_pressionar)
    campo.bind("<KeyRelease>", ao_soltar)
    ttk.Button(quadro, text="Limpar", width=7, command=limpar).pack(side="left", padx=(4, 0))
    quadro.obter = lambda: estado["valor"]
    return quadro


# --------------------------------------------------------------------------
# Aplicativo: janela principal, bandeja e configurações
# --------------------------------------------------------------------------

class App:
    def __init__(self, cfg, ctrl, atalhos, pedidos, minimizado):
        import tkinter as tk
        self.cfg, self.ctrl, self.atalhos, self.pedidos = cfg, ctrl, atalhos, pedidos
        self.raiz = tk.Tk()
        self.raiz.title("Controle de Brilho")
        self.raiz.resizable(False, False)
        self.icone_ico = Path(getattr(sys, "_MEIPASS", PASTA)) / "icone.ico"
        self._aplicar_icone(self.raiz)
        # Ctrl+A seleciona todo o texto dos campos (padrão do Windows)
        for classe in ("TEntry", "TSpinbox"):
            self.raiz.bind_class(classe, "<Control-a>", lambda e: (
                e.widget.select_range(0, "end"), e.widget.icursor("end"), "break")[-1])

        self.bandeja = self._criar_bandeja()
        if minimizado and self.bandeja:
            self.raiz.withdraw()
        # Fechar a janela só a esconde; o programa continua na bandeja.
        self.raiz.protocol("WM_DELETE_WINDOW", self.raiz.withdraw if self.bandeja else self.sair)

        self.janela_config = None
        self.quadro = None
        self.construir()
        self.sincronizar()

    def _aplicar_icone(self, janela):
        if self.icone_ico.exists() and sys.platform == "win32":
            try:
                janela.iconbitmap(str(self.icone_ico))
            except Exception:
                pass

    # ---------------- bandeja ----------------

    def _menu_bandeja(self):
        import pystray
        ctrl, pedidos = self.ctrl, self.pedidos

        def item_preset(p):
            texto = p.get("nome", "?")
            if p.get("atalho"):
                texto += f"\t{descrever_atalho(p['atalho'])}"
            return pystray.MenuItem(texto, lambda: ctrl.aplicar(p.get("brilho")))

        def alternar_autostart():
            novo = not autostart_ativo()
            definir_autostart(novo)
            self.cfg["iniciar_com_windows"] = novo
            salvar_opcao("iniciar_com_windows", novo)

        itens = [
            pystray.MenuItem("Abrir controles", lambda: pedidos.put("mostrar"), default=True),
            pystray.Menu.SEPARATOR,
            *[item_preset(p) for p in self.cfg.get("presets", [])],
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Aumentar {ctrl.passo}%", lambda: ctrl.ajustar(+1)),
            pystray.MenuItem(f"Diminuir {ctrl.passo}%", lambda: ctrl.ajustar(-1)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Configurações…", lambda: pedidos.put("config")),
        ]
        if sys.platform == "win32":
            itens.append(pystray.MenuItem("Iniciar com o Windows", alternar_autostart,
                                          checked=lambda item: autostart_ativo()))
        itens.append(pystray.MenuItem("Sair", lambda: pedidos.put("sair")))
        return pystray.Menu(*itens)

    def _criar_bandeja(self):
        try:
            import pystray
            icone = pystray.Icon("ControleBrilho", imagem_icone(), "Controle de Brilho",
                                 self._menu_bandeja())
            icone.run_detached()
            return icone
        except Exception as e:
            avisar(f"Ícone na bandeja indisponível: {e}", erro=True)
            return None

    # ---------------- janela principal ----------------

    def construir(self):
        import tkinter as tk
        from tkinter import ttk
        ctrl = self.ctrl
        if self.quadro is not None:
            self.quadro.destroy()
        self.quadro = quadro = ttk.Frame(self.raiz, padding=14)
        quadro.grid()
        self.barras = []
        self.barra_todos = None
        self.estado_todos = {"ultimo": None}
        self._ultimo = None

        def ao_mover(indice):
            def callback(valor):
                valor = int(float(valor))
                if valor != ctrl.valores[indice]:  # ignora quando foi a sincronização
                    ctrl.definir(indice, valor)
            return callback

        largura = max([12] + [len(m.nome) + 2 for m in ctrl.monitores])
        linha = 0
        for i, mon in enumerate(ctrl.monitores):
            ttk.Label(quadro, text=mon.nome, width=largura).grid(row=linha, column=0, sticky="w")
            barra = tk.Scale(quadro, from_=0, to=100, orient="horizontal", length=280,
                             resolution=1, command=ao_mover(i))
            barra.set(ctrl.valores[i])
            barra.grid(row=linha, column=1, pady=2)
            self.barras.append(barra)
            linha += 1

        if len(ctrl.monitores) > 1:
            ttk.Separator(quadro).grid(row=linha, column=0, columnspan=2, sticky="ew", pady=6)
            linha += 1
            ttk.Label(quadro, text="Todos", width=largura).grid(row=linha, column=0, sticky="w")

            def mover_todos(valor):
                valor = int(float(valor))
                if valor != self.estado_todos["ultimo"]:
                    self.estado_todos["ultimo"] = valor
                    ctrl.aplicar(valor)

            self.barra_todos = tk.Scale(quadro, from_=0, to=100, orient="horizontal",
                                        length=280, resolution=1, command=mover_todos)
            self.barra_todos.grid(row=linha, column=1, pady=2)
            linha += 1

        botoes = ttk.Frame(quadro)
        botoes.grid(row=linha, column=0, columnspan=2, pady=(10, 0))
        for n, p in enumerate(self.cfg.get("presets", [])):
            texto = p.get("nome", "?")
            if p.get("atalho"):
                texto += f"\n{descrever_atalho(p['atalho'])}"
            ttk.Button(botoes, text=texto, width=12,
                       command=lambda b=p.get("brilho"): ctrl.aplicar(b)
                       ).grid(row=n // 4, column=n % 4, padx=3, pady=3)
        linha += 1

        rodape = ttk.Frame(quadro)
        rodape.grid(row=linha, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        atalhos = self.cfg.get("atalhos", {})
        dicas = []
        if atalhos.get("aumentar"):
            dicas.append(f"{descrever_atalho(atalhos['aumentar'])}: +{ctrl.passo}%")
        if atalhos.get("diminuir"):
            dicas.append(f"{descrever_atalho(atalhos['diminuir'])}: −{ctrl.passo}%")
        ttk.Label(rodape, text="   ".join(dicas), foreground="gray").pack(side="left")
        ttk.Button(rodape, text="⚙ Configurações…", command=self.abrir_config).pack(side="right")

    def mostrar(self):
        r = self.raiz
        r.deiconify()
        r.lift()
        r.attributes("-topmost", True)
        r.after(200, lambda: r.attributes("-topmost", False))
        r.focus_force()

    def sair(self):
        if self.bandeja:
            self.bandeja.stop()
        self.raiz.destroy()

    def sincronizar(self):
        """Executa pedidos de outras threads e acompanha mudanças de brilho."""
        while True:
            try:
                pedido = self.pedidos.get_nowait()
            except queue.Empty:
                break
            if pedido == "mostrar":
                self.mostrar()
            elif pedido == "config":
                self.abrir_config()
            elif pedido == "sair":
                self.sair()
                return

        valores = list(self.ctrl.valores)
        if valores != self._ultimo:
            self._ultimo = valores
            for barra, v in zip(self.barras, valores):
                if int(barra.get()) != v:
                    barra.set(v)
            if self.barra_todos is not None:
                media = round(sum(valores) / len(valores))
                if media != self.estado_todos["ultimo"]:
                    self.estado_todos["ultimo"] = media
                    self.barra_todos.set(media)
            if self.bandeja:
                self.bandeja.title = texto_dica(self.ctrl)
        self.raiz.after(150, self.sincronizar)

    def run(self):
        self.raiz.mainloop()

    # ---------------- configurações ----------------

    def abrir_config(self):
        if self.janela_config is not None and self.janela_config.winfo_exists():
            self.janela_config.deiconify()
            self.janela_config.lift()
            self.janela_config.focus_force()
            return
        self.janela_config = JanelaConfig(self)

    def aplicar_config(self, novo):
        """Salva e aplica as novas configurações sem reiniciar o programa."""
        self.cfg.clear()
        self.cfg.update(novo)
        salvar_config(self.cfg)
        nomes = self.cfg.get("nomes_monitores") or []
        for i, mon in enumerate(self.ctrl.monitores):
            if i < len(nomes) and nomes[i]:
                mon.nome = nomes[i]
        self.ctrl.passo = int(self.cfg.get("passo", 10))
        self.atalhos.carregar(self.cfg)
        if sys.platform == "win32":
            definir_autostart(bool(self.cfg.get("iniciar_com_windows", True)))
        self.construir()
        if self.bandeja:
            self.bandeja.menu = self._menu_bandeja()
            self.bandeja.title = texto_dica(self.ctrl)


class JanelaConfig:
    def __init__(self, app):
        import copy
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.n = len(app.ctrl.monitores)
        self.presets = copy.deepcopy(app.cfg.get("presets", []))

        self.janela = j = tk.Toplevel(app.raiz)
        j.withdraw()  # monta escondida, centraliza e só então mostra
        j.title("Configurações — Controle de Brilho")
        j.resizable(False, False)
        app._aplicar_icone(j)
        j.protocol("WM_DELETE_WINDOW", self.fechar)
        self.winfo_exists = j.winfo_exists
        self.deiconify, self.lift, self.focus_force = j.deiconify, j.lift, j.focus_force
        corpo = ttk.Frame(j, padding=12)
        corpo.grid(sticky="nsew")

        # Monitores
        f_mon = ttk.LabelFrame(corpo, text="Monitores", padding=8)
        f_mon.grid(row=0, column=0, sticky="ew")
        self.nomes = []
        for i, mon in enumerate(app.ctrl.monitores):
            ttk.Label(f_mon, text=f"Monitor {i + 1}:").grid(row=i, column=0, sticky="w", padx=(0, 6))
            var = tk.StringVar(value=mon.nome)
            ttk.Entry(f_mon, textvariable=var, width=24).grid(row=i, column=1, sticky="w", pady=2)
            self.nomes.append(var)

        # Presets
        f_pre = ttk.LabelFrame(corpo, text="Presets", padding=8)
        f_pre.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        colunas = ["nome"] + [f"m{i}" for i in range(self.n)] + ["atalho"]
        self.tabela = ttk.Treeview(f_pre, columns=colunas, show="headings", height=7,
                                   selectmode="browse")
        self.tabela.heading("nome", text="Nome")
        self.tabela.column("nome", width=130)
        for i, mon in enumerate(app.ctrl.monitores):
            self.tabela.heading(f"m{i}", text=mon.nome)
            self.tabela.column(f"m{i}", width=80, anchor="center")
        self.tabela.heading("atalho", text="Atalho")
        self.tabela.column("atalho", width=120, anchor="center")
        self.tabela.grid(row=0, column=0, columnspan=6, sticky="ew")
        self.tabela.bind("<Double-1>", lambda e: self.editar())
        for c, (txt, cmd) in enumerate([("Adicionar", self.adicionar), ("Editar", self.editar),
                                        ("Remover", self.remover), ("▲", lambda: self.mover(-1)),
                                        ("▼", lambda: self.mover(+1))]):
            ttk.Button(f_pre, text=txt, command=cmd, width=10 if len(txt) > 1 else 3).grid(
                row=1, column=c, pady=(6, 0), padx=(0, 4), sticky="w")
        self.atualizar_tabela()

        # Atalhos de ajuste
        f_at = ttk.LabelFrame(corpo, text="Atalhos de ajuste", padding=8)
        f_at.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        atalhos = app.cfg.get("atalhos", {})
        ttk.Label(f_at, text="Aumentar brilho:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.campo_aumentar = criar_campo_atalho(f_at, app.atalhos, atalhos.get("aumentar"))
        self.campo_aumentar.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(f_at, text="Diminuir brilho:").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.campo_diminuir = criar_campo_atalho(f_at, app.atalhos, atalhos.get("diminuir"))
        self.campo_diminuir.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f_at, text="Passo (%):").grid(row=2, column=0, sticky="w", padx=(0, 6))
        self.passo = tk.IntVar(value=int(app.cfg.get("passo", 10)))
        ttk.Spinbox(f_at, from_=1, to=50, textvariable=self.passo, width=6).grid(
            row=2, column=1, sticky="w", pady=2)
        ttk.Label(f_at, text="Clique num campo e pressione a combinação (Esc cancela).",
                  foreground="gray").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Geral
        self.autostart = tk.BooleanVar(value=bool(app.cfg.get("iniciar_com_windows", True)))
        if sys.platform == "win32":
            ttk.Checkbutton(corpo, text="Iniciar com o Windows (minimizado na bandeja)",
                            variable=self.autostart).grid(row=3, column=0, sticky="w", pady=(10, 0))

        botoes = ttk.Frame(corpo)
        botoes.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(botoes, text="Abrir pasta das configurações",
                   command=self.abrir_pasta).pack(side="left")
        ttk.Button(botoes, text="Cancelar", command=self.fechar).pack(side="right")
        ttk.Button(botoes, text="Salvar", command=self.salvar).pack(side="right", padx=(0, 6))

        centralizar(j)
        j.deiconify()
        j.lift()
        j.focus_force()

    def abrir_pasta(self):
        pasta = ARQUIVO_CONFIG.parent
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(pasta))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(pasta)])
        except Exception as e:
            from tkinter import messagebox
            messagebox.showinfo("Configurações", f"As configurações ficam em:\n{ARQUIVO_CONFIG}\n\n({e})",
                                parent=self.janela)

    # ---- presets ----

    def atualizar_tabela(self, selecionar=None):
        self.tabela.delete(*self.tabela.get_children())
        for i, p in enumerate(self.presets):
            valores = [texto_valor(v) for v in valores_do_preset(p.get("brilho"), self.n)]
            self.tabela.insert("", "end", iid=str(i), values=[p.get("nome", "")] + valores
                               + [descrever_atalho(p.get("atalho")) or "—"])
        if selecionar is not None and 0 <= selecionar < len(self.presets):
            self.tabela.selection_set(str(selecionar))
            self.tabela.see(str(selecionar))

    def _selecionado(self):
        sel = self.tabela.selection()
        return int(sel[0]) if sel else None

    def adicionar(self):
        novo = {"nome": "", "atalho": "", "brilho": 50}

        def ok(p):
            self.presets.append(p)
            self.atualizar_tabela(len(self.presets) - 1)
        DialogoPreset(self, novo, ok, titulo="Novo preset")

    def editar(self):
        i = self._selecionado()
        if i is None:
            return

        def ok(p):
            self.presets[i] = p
            self.atualizar_tabela(i)
        DialogoPreset(self, self.presets[i], ok, titulo="Editar preset")

    def remover(self):
        i = self._selecionado()
        if i is None:
            return
        del self.presets[i]
        self.atualizar_tabela(min(i, len(self.presets) - 1))

    def mover(self, delta):
        i = self._selecionado()
        if i is None or not 0 <= i + delta < len(self.presets):
            return
        self.presets[i], self.presets[i + delta] = self.presets[i + delta], self.presets[i]
        self.atualizar_tabela(i + delta)

    # ---- salvar ----

    def salvar(self):
        from tkinter import messagebox
        app = self.app
        nomes_presets = [p.get("nome", "").strip() for p in self.presets]
        if any(not n for n in nomes_presets):
            messagebox.showerror("Configurações", "Todo preset precisa de um nome.", parent=self.janela)
            return
        repetidos = {n for n in nomes_presets if nomes_presets.count(n) > 1}
        if repetidos:
            messagebox.showerror("Configurações", f"Nome de preset repetido: {', '.join(repetidos)}",
                                 parent=self.janela)
            return

        usados = {}
        for descricao, atalho in ([("Aumentar brilho", self.campo_aumentar.obter()),
                                   ("Diminuir brilho", self.campo_diminuir.obter())]
                                  + [(f"Preset {p['nome']}", p.get("atalho")) for p in self.presets]):
            if not atalho:
                continue
            if atalho in usados:
                messagebox.showerror(
                    "Configurações",
                    f"O atalho {descrever_atalho(atalho)} está em \"{usados[atalho]}\" e em "
                    f"\"{descricao}\". Escolha atalhos diferentes.", parent=self.janela)
                return
            usados[atalho] = descricao

        try:
            passo = max(1, min(50, int(self.passo.get())))
        except Exception:
            passo = 10

        nomes_antigos = list(app.cfg.get("nomes_monitores") or [])
        nomes = [v.get().strip() or f"Monitor {i + 1}" for i, v in enumerate(self.nomes)]
        nomes += nomes_antigos[len(nomes):]  # preserva nomes de monitores não conectados

        novo = dict(app.cfg)
        novo.update({
            "iniciar_com_windows": bool(self.autostart.get()),
            "passo": passo,
            "nomes_monitores": nomes,
            "atalhos": {"aumentar": self.campo_aumentar.obter(),
                        "diminuir": self.campo_diminuir.obter()},
            "presets": self.presets,
        })
        app.aplicar_config(novo)
        self.fechar()

    def fechar(self):
        self.app.atalhos.pausado = False
        self.janela.destroy()


class DialogoPreset:
    def __init__(self, cfg_janela, preset, ao_confirmar, titulo):
        import tkinter as tk
        from tkinter import ttk
        app = cfg_janela.app
        self.ao_confirmar = ao_confirmar
        self.original = preset
        self.n = cfg_janela.n

        self.janela = j = tk.Toplevel(cfg_janela.janela)
        j.withdraw()
        j.title(titulo)
        j.resizable(False, False)
        j.transient(cfg_janela.janela)
        app._aplicar_icone(j)
        corpo = ttk.Frame(j, padding=12)
        corpo.grid()

        ttk.Label(corpo, text="Nome:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.nome = tk.StringVar(value=preset.get("nome", ""))
        entrada = ttk.Entry(corpo, textvariable=self.nome, width=28)
        entrada.grid(row=0, column=1, columnspan=3, sticky="w", pady=(0, 6))

        valores = valores_do_preset(preset.get("brilho"), self.n)
        self.escalas, self.manter = [], []
        for i, mon in enumerate(app.ctrl.monitores):
            ttk.Label(corpo, text=f"{mon.nome}:").grid(row=1 + i, column=0, sticky="w", padx=(0, 8))
            escala = tk.Scale(corpo, from_=0, to=100, orient="horizontal", length=220, resolution=1)
            escala.set(50 if valores[i] is None else valores[i])
            escala.grid(row=1 + i, column=1, sticky="w")
            manter = tk.BooleanVar(value=valores[i] is None)

            def alternar(e=escala, m=manter):
                e.configure(state="disabled" if m.get() else "normal",
                            fg="gray" if m.get() else "black")
            ttk.Checkbutton(corpo, text="não alterar", variable=manter, command=alternar).grid(
                row=1 + i, column=2, sticky="w", padx=(8, 0))
            alternar()
            self.escalas.append(escala)
            self.manter.append(manter)

        linha = 1 + self.n
        ttk.Label(corpo, text="Atalho:").grid(row=linha, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.campo = criar_campo_atalho(corpo, app.atalhos, preset.get("atalho"))
        self.campo.grid(row=linha, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(corpo, text="Clique no campo e pressione a combinação (Esc cancela).",
                  foreground="gray").grid(row=linha + 1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.erro = ttk.Label(corpo, text="", foreground="#c0392b")
        self.erro.grid(row=linha + 2, column=0, columnspan=4, sticky="w")
        botoes = ttk.Frame(corpo)
        botoes.grid(row=linha + 3, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(botoes, text="Cancelar", command=self.fechar).pack(side="right")
        ttk.Button(botoes, text="OK", command=self.confirmar).pack(side="right", padx=(0, 6))
        j.protocol("WM_DELETE_WINDOW", self.fechar)
        j.bind("<Return>", lambda e: self.confirmar())

        self.app = app
        centralizar(j, cfg_janela.janela)
        j.deiconify()
        j.wait_visibility()
        j.grab_set()
        entrada.focus_set()

    def confirmar(self):
        nome = self.nome.get().strip()
        if not nome:
            self.erro.configure(text="Digite um nome para o preset.")
            return
        valores = [None if m.get() else int(e.get()) for e, m in zip(self.escalas, self.manter)]
        if all(v is None for v in valores):
            self.erro.configure(text="Defina o brilho de pelo menos um monitor.")
            return
        if None not in valores and len(set(valores)) == 1:
            brilho = valores[0]  # mesmo valor para todos: salva um número só
        else:
            brilho = valores
            antigo = self.original.get("brilho")
            if isinstance(antigo, list) and len(antigo) > self.n:
                brilho = valores + antigo[self.n:]  # preserva monitores não conectados
        preset = dict(self.original)
        preset.update({"nome": nome, "brilho": brilho, "atalho": self.campo.obter()})
        self.fechar()
        self.ao_confirmar(preset)

    def fechar(self):
        self.app.atalhos.pausado = False
        self.janela.grab_release()
        self.janela.destroy()


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Controle de brilho de monitores externos (DDC/CI).")
    ap.add_argument("--listar", action="store_true", help="lista monitores e brilho atual")
    ap.add_argument("--definir", nargs="+", type=int, metavar="N",
                    help="define o brilho (um valor para todos ou um por monitor)")
    ap.add_argument("--preset", metavar="NOME", help="aplica um preset do config.json")
    ap.add_argument("--minimizado", action="store_true",
                    help="inicia só com o ícone na bandeja, sem abrir a janela")
    ap.add_argument("--sem-janela", action="store_true",
                    help="roda só os atalhos globais, sem janela nem bandeja")
    ap.add_argument("--simular", action="store_true", help="usa monitores falsos (teste)")
    args = ap.parse_args()

    cfg = carregar_config()
    comando_direto = args.listar or args.definir or args.preset

    pedidos = queue.Queue()
    if not comando_direto and not reservar_instancia(pedidos, mostrar=not args.minimizado):
        return  # já está rodando; a outra instância mostra a janela

    # Ao iniciar com o Windows, os monitores podem demorar a responder.
    monitores = detectar_monitores(cfg, args.simular, tentativas=12 if args.minimizado else 1)

    # Comandos diretos: aplicam e saem (úteis para atalhos do próprio sistema)
    if args.listar:
        avisar("\n".join(f"{i}. {m.nome}: {m.ler()}%" for i, m in enumerate(monitores, 1)))
        return

    if args.definir or args.preset:
        if args.preset:
            preset = next((p for p in cfg["presets"]
                           if p["nome"].lower() == args.preset.lower()), None)
            if not preset:
                nomes = ", ".join(p["nome"] for p in cfg["presets"])
                falhar(f"Preset '{args.preset}' não existe. Disponíveis: {nomes}")
            brilho = preset["brilho"]
        else:
            brilho = args.definir[0] if len(args.definir) == 1 else args.definir
        if isinstance(brilho, (int, float)):
            brilho = [brilho] * len(monitores)
        for m, valor in zip(monitores, brilho):
            if valor is not None:
                m.definir(max(0, min(100, int(valor))))
        return

    # Mantém o registro do início automático em dia (inclusive se o .exe mudar de pasta).
    if sys.platform == "win32" and not args.simular:
        definir_autostart(bool(cfg.get("iniciar_com_windows", True)))

    ctrl = Controlador(monitores, cfg.get("passo", 10))
    atalhos = GerenciadorAtalhos(ctrl)
    atalhos.carregar(cfg)

    if args.sem_janela:
        if not atalhos.ativo:
            falhar("Nenhum atalho ativo; nada a fazer.")
        print("Atalhos ativos. Ctrl+C para sair.")
        try:
            atalhos.aguardar()
        except KeyboardInterrupt:
            pass
        return

    App(cfg, ctrl, atalhos, pedidos, args.minimizado).run()


if __name__ == "__main__":
    main()
