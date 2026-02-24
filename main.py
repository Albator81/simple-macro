import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import json
import os
import sys
import select
import evdev
from evdev import UInput, ecodes, InputDevice, list_devices

# --- Configuration ---
CONFIG_FILE = "data.json"

# --- Permission Check ---
def check_root():
    if os.geteuid() != 0:
        messagebox.showerror("Permission Error", 
            "Universal Linux Input requires root privileges.\n\n"
            "Please run this application with sudo.")
        sys.exit(1)

# --- Engine: Virtual Device & Mapping ---
class LinuxInputEngine:
    def __init__(self):
        self.binding_mode = False 
        self.bind_callback = None # Function to call when binding is done
        
        # Define Modifiers (Wait for more input if these are pressed)
        self.MODIFIERS = {
            ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
            ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
            ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
            ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
            ecodes.KEY_CAPSLOCK, ecodes.KEY_NUMLOCK, ecodes.KEY_SCROLLLOCK,
            ecodes.KEY_COMPOSE
        }

        # --- Filter strictly for valid key codes ---
        valid_keys = []
        key_max = getattr(ecodes, 'KEY_MAX', 0x2ff)
        
        for k in dir(ecodes):
            if k.startswith('KEY_') or k.startswith('BTN_'):
                val = getattr(ecodes, k)
                if isinstance(val, int) and 0 <= val < key_max:
                    valid_keys.append(val)

        # --- Setup Virtual Device ---
        cap = {
            ecodes.EV_KEY: valid_keys,
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL],
        }
        
        try:
            self.uinput = UInput(cap, name="MacroStudio-Virtual-Device")
        except PermissionError:
            check_root()
        except OSError as e:
            print(f"Error creating virtual device: {e}")
            sys.exit(1)

        self.running = True
        self.pressed_keys = set()
        self.active_macros = self.load_macros()
        
        # Mappings
        self.str_to_code = {}
        for k in dir(ecodes):
            if k.startswith('KEY_'):
                self.str_to_code[k.replace('KEY_', '').lower()] = getattr(ecodes, k)
        
        self.str_to_code['left_click'] = ecodes.BTN_LEFT
        self.str_to_code['right_click'] = ecodes.BTN_RIGHT
        self.code_to_str = {v: k for k, v in self.str_to_code.items()}

    def load_macros(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_macros(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.active_macros, f, indent=4)

    def find_keyboards(self):
        try:
            devices = [InputDevice(path) for path in list_devices()]
            keyboards = []
            for dev in devices:
                cap = dev.capabilities()
                if ecodes.EV_KEY in cap:
                    if "MacroStudio" in dev.name: continue
                    keyboards.append(dev)
            return keyboards
        except Exception:
            return []

    def listen_loop(self):
        devices = self.find_keyboards()
        fds = {dev.fd: dev for dev in devices}

        while self.running:
            try:
                r, _, _ = select.select(fds, [], [], 0.5)
            except Exception:
                continue
            
            for fd in r:
                try:
                    for event in fds[fd].read():
                        if event.type == ecodes.EV_KEY:
                            key_name = self.code_to_str.get(event.code, f"unk_{event.code}")
                            
                            if event.value == 1: # Key Down
                                self.pressed_keys.add(key_name)
                                
                                if self.binding_mode:
                                    # SMART LOGIC: 
                                    # If modifier -> Keep listening (ctrl...)
                                    # If normal key -> Finish immediately (ctrl+s!)
                                    if event.code not in self.MODIFIERS:
                                        if self.bind_callback:
                                            self.bind_callback()
                                else:
                                    self.check_trigger()
                                    
                            elif event.value == 0: # Key Up
                                if not self.binding_mode:
                                    self.pressed_keys.discard(key_name)
                except OSError:
                    del fds[fd]

    def check_trigger(self):
        if not self.pressed_keys: return
        trigger = "+".join(sorted(self.pressed_keys))
        if trigger in self.active_macros:
            macro = self.active_macros[trigger]
            threading.Thread(target=self.execute_macro, args=(macro,), daemon=True).start()

    def execute_macro(self, macro_data):
        actions = macro_data.get("actions", [])
        repeat = macro_data.get("repeat", 1)
        for _ in range(repeat):
            for action_type, value in actions:
                time.sleep(0.05)
                try:
                    if action_type == "Key Input": self.inject_keys(value)
                    elif action_type == "Wait": time.sleep(float(value))
                    elif action_type == "Mouse Move": self.inject_mouse(value)
                except: pass

    def inject_keys(self, key_string):
        keys = key_string.split("+")
        codes = []
        for k in keys:
            code = self.str_to_code.get(k.lower())
            if code: codes.append(code)
        
        if not codes: return
        for c in codes: self.uinput.write(ecodes.EV_KEY, c, 1)
        self.uinput.syn()
        time.sleep(0.02)
        for c in reversed(codes): self.uinput.write(ecodes.EV_KEY, c, 0)
        self.uinput.syn()

    def inject_mouse(self, value_str):
        try:
            action, coords = value_str.split(";", 1)
            if action == "Move by":
                x, y = map(int, coords.split(";"))
                self.uinput.write(ecodes.EV_REL, ecodes.REL_X, x)
                self.uinput.write(ecodes.EV_REL, ecodes.REL_Y, y)
                self.uinput.syn()
            elif action == "Left Click":
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
                self.uinput.syn()
                time.sleep(0.05)
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
                self.uinput.syn()
        except: pass

# --- Global Engine Instance ---
engine = None

# --- GUI Application ---
class MacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Universal Linux Macro Studio (Root)")
        self.root.geometry("700x550")
        self.current_sequence = []
        
        style = ttk.Style()
        style.theme_use('clam')

        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill="both", expand=True)

        # Creator
        creator_frame = ttk.LabelFrame(main_frame, text=" Macro Creator ", padding="10")
        creator_frame.pack(side="left", fill="both", expand=True, padx=5)

        ttk.Label(creator_frame, text="Trigger Combo:").pack(anchor="w")
        self.trigger_var = tk.StringVar(value="Click to bind...")
        self.trigger_btn = ttk.Button(creator_frame, textvariable=self.trigger_var, command=self.bind_trigger)
        self.trigger_btn.pack(fill="x", pady=2)
        
        ttk.Label(creator_frame, text="Repeat Count:").pack(anchor="w", pady=(10,0))
        self.repeat_count = ttk.Spinbox(creator_frame, from_=1, to=999)
        self.repeat_count.set(1)
        self.repeat_count.pack(fill="x")

        ttk.Label(creator_frame, text="Action Type:").pack(anchor="w", pady=(10,0))
        self.event_type = ttk.Combobox(creator_frame, values=["Key Input", "Wait", "Mouse Move"], state="readonly")
        self.event_type.set("Key Input")
        self.event_type.bind("<<ComboboxSelected>>", self.on_event_change)
        self.event_type.pack(fill="x")

        self.options_frame = ttk.Frame(creator_frame, borderwidth=1, relief="solid", padding=5)
        self.options_frame.pack(fill="x", pady=10)
        
        # Variables for action storage
        self.captured_action_combo = ""
        self.action_key_var = tk.StringVar(value="Record Keystrokes")
        
        self.render_options("Key Input")

        ttk.Button(creator_frame, text="Add Action", command=self.add_action).pack(fill="x", pady=5)
        self.seq_list = tk.Listbox(creator_frame, height=8)
        self.seq_list.pack(fill="x")
        ttk.Button(creator_frame, text="Clear Sequence", command=self.clear_seq).pack(fill="x", pady=2)
        ttk.Button(creator_frame, text="SAVE MACRO", command=self.save_macro).pack(fill="x", pady=(15, 0))

        # Dashboard
        dash_frame = ttk.LabelFrame(main_frame, text=" Active Macros ", padding="10")
        dash_frame.pack(side="right", fill="both", expand=True, padx=5)
        self.macro_list = tk.Listbox(dash_frame)
        self.macro_list.pack(fill="both", expand=True)
        ttk.Button(dash_frame, text="Delete Selected", command=self.delete_macro).pack(pady=5)
        
        self.refresh_dashboard()
        self.binding_timer = None

    def render_options(self, event_type):
        for widget in self.options_frame.winfo_children(): widget.destroy()
        
        if event_type == "Key Input":
            # REPLACED MANUAL ENTRY WITH BIND BUTTON
            ttk.Label(self.options_frame, text="Action Keys:").pack(anchor="w")
            self.action_key_btn = ttk.Button(self.options_frame, textvariable=self.action_key_var, command=self.bind_action_key)
            self.action_key_btn.pack(fill="x")
            
        elif event_type == "Wait":
            ttk.Label(self.options_frame, text="Seconds:").pack(anchor="w")
            self.wait_entry = ttk.Entry(self.options_frame)
            self.wait_entry.insert(0, "0.5")
            self.wait_entry.pack(fill="x")
            
        elif event_type == "Mouse Move":
            ttk.Label(self.options_frame, text="Action:").pack(anchor="w")
            self.mouse_action = ttk.Combobox(self.options_frame, values=["Move by", "Left Click"], state="readonly")
            self.mouse_action.set("Move by")
            self.mouse_action.pack(fill="x")
            ttk.Label(self.options_frame, text="X;Y (e.g. 100;100):").pack(anchor="w")
            self.mouse_coords = ttk.Entry(self.options_frame)
            self.mouse_coords.insert(0, "100;100")
            self.mouse_coords.pack(fill="x")

    def on_event_change(self, event):
        self.render_options(self.event_type.get())

    # --- TRIGGER BINDING LOGIC ---
    def bind_trigger(self):
        if engine.binding_mode: return
        engine.pressed_keys.clear()
        engine.binding_mode = True
        
        self.trigger_var.set("Press Trigger Keys...")
        self.trigger_btn.config(state="disabled")
        
        engine.bind_callback = lambda: self.root.after(0, self._finalize_trigger)
        self.binding_timer = self.root.after(5000, self._finalize_trigger)

    def _finalize_trigger(self):
        if self.binding_timer: self.root.after_cancel(self.binding_timer)
        engine.binding_mode = False
        engine.bind_callback = None
        
        if not engine.pressed_keys:
            self.trigger_var.set("Failed. Try again")
        else:
            combo = "+".join(sorted(engine.pressed_keys))
            self.trigger_var.set(combo)
        self.trigger_btn.config(state="normal")

    # --- ACTION KEY BINDING LOGIC (NEW) ---
    def bind_action_key(self):
        if engine.binding_mode: return
        engine.pressed_keys.clear()
        engine.binding_mode = True
        
        self.action_key_var.set("Press Action Keys...")
        self.action_key_btn.config(state="disabled")
        
        engine.bind_callback = lambda: self.root.after(0, self._finalize_action_key)
        self.binding_timer = self.root.after(5000, self._finalize_action_key)

    def _finalize_action_key(self):
        if self.binding_timer: self.root.after_cancel(self.binding_timer)
        engine.binding_mode = False
        engine.bind_callback = None
        
        if not engine.pressed_keys:
            self.action_key_var.set("Record Keystrokes")
            self.captured_action_combo = ""
        else:
            combo = "+".join(sorted(engine.pressed_keys))
            self.action_key_var.set(combo)
            self.captured_action_combo = combo
        
        self.action_key_btn.config(state="normal")

    def add_action(self):
        etype = self.event_type.get()
        val = ""
        
        if etype == "Key Input":
            # Use the captured combo variable
            val = self.captured_action_combo
            if not val:
                messagebox.showwarning("Error", "Please record keys first!")
                return
        elif etype == "Wait":
            val = self.wait_entry.get()
        elif etype == "Mouse Move":
            val = f"{self.mouse_action.get()};{self.mouse_coords.get()}"
            
        if val:
            self.current_sequence.append((etype, val))
            self.seq_list.insert(tk.END, f"{etype}: {val}")
            
            # Reset Action UI slightly for convenience
            if etype == "Key Input":
                self.captured_action_combo = ""
                self.action_key_var.set("Record Keystrokes")

    def clear_seq(self):
        self.current_sequence = []
        self.seq_list.delete(0, tk.END)

    def save_macro(self):
        trigger = self.trigger_var.get()
        if "..." in trigger or "Failed" in trigger or not trigger: return
        if not self.current_sequence: return
        engine.active_macros[trigger] = {
            "actions": self.current_sequence,
            "repeat": int(self.repeat_count.get())
        }
        engine.save_macros()
        self.refresh_dashboard()
        self.clear_seq()

    def refresh_dashboard(self):
        self.macro_list.delete(0, tk.END)
        for k in engine.active_macros.keys(): self.macro_list.insert(tk.END, k)

    def delete_macro(self):
        sel = self.macro_list.curselection()
        if sel:
            key = self.macro_list.get(sel[0])
            del engine.active_macros[key]
            engine.save_macros()
            self.refresh_dashboard()

if __name__ == "__main__":
    check_root()
    engine = LinuxInputEngine()
    root = tk.Tk()
    app = MacroApp(root)
    t = threading.Thread(target=engine.listen_loop, daemon=True)
    t.start()
    try: root.mainloop()
    except KeyboardInterrupt: pass
    finally:
        engine.running = False
        if engine.uinput: engine.uinput.close()