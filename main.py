import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
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
        
        # Define Modifiers
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
        self.data = self.load_data()
        self.active_toggles = {} # Stores flags for toggle macros
        
        # Mappings
        self.str_to_code = {}
        for k in dir(ecodes):
            if k.startswith('KEY_'):
                self.str_to_code[k.replace('KEY_', '').lower()] = getattr(ecodes, k)
        
        self.str_to_code['left_click'] = ecodes.BTN_LEFT
        self.str_to_code['right_click'] = ecodes.BTN_RIGHT
        self.code_to_str = {v: k for k, v in self.str_to_code.items()}

    def load_data(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    # Migration check
                    if "profiles" not in data:
                        # Convert old format to profiles
                        old_bindings = data.get("bindings", {})
                        data = {
                            "builds": data.get("builds", {}),
                            "profiles": {"Default": old_bindings},
                            "current_profile": "Default"
                        }
                    return data
            except:
                return {"builds": {}, "profiles": {"Default": {}}, "current_profile": "Default"}
        return {"builds": {}, "profiles": {"Default": {}}, "current_profile": "Default"}

    def save_data(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.data, f, indent=4)

    def find_keyboards(self):
        try:
            devices = [InputDevice(path) for path in list_devices()]
            keyboards = []
            for dev in devices:
                cap = dev.capabilities()
                if ecodes.EV_KEY in cap:
                    if "MacroStudio" in dev.name: continue
                    # Check if it has keys (not just mouse buttons)
                    if ecodes.KEY_A in cap.get(ecodes.EV_KEY, []):
                        keyboards.append(dev)
            return keyboards
        except Exception:
            return []

    def find_mice(self):
        try:
            devices = [InputDevice(path) for path in list_devices()]
            mice = []
            for dev in devices:
                cap = dev.capabilities()
                # Check for relative movement (mouse)
                if ecodes.EV_REL in cap:
                    mice.append(dev)
            return mice
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
        current_profile = self.data.get("current_profile", "Default")
        bindings = self.data["profiles"].get(current_profile, {})
        
        if trigger in bindings:
            build_name = bindings[trigger]
            if build_name in self.data["builds"]:
                macro = self.data["builds"][build_name]
                mode = macro.get("mode", "Repeat")
                
                # TOGGLE LOGIC
                if mode == "Toggle":
                    if build_name in self.active_toggles and self.active_toggles[build_name]:
                        # Stop it
                        self.active_toggles[build_name] = False
                        if app: app.root.after(0, lambda: app.notify(f"[{current_profile}] Stopped: {build_name}"))
                    else:
                        # Start it
                        self.active_toggles[build_name] = True
                        if app: app.root.after(0, lambda: app.notify(f"[{current_profile}] Toggled ON: {build_name}"))
                        threading.Thread(target=self.execute_toggle, args=(build_name, macro), daemon=True).start()
                else:
                    # Normal Execution
                    if app:
                        app.root.after(0, lambda: app.notify(f"[{current_profile}] Executing: {build_name}"))
                    threading.Thread(target=self.execute_macro, args=(macro,), daemon=True).start()

    def execute_toggle(self, name, macro_data):
        actions = macro_data.get("actions", [])
        while self.active_toggles.get(name, False):
            for action_type, value in actions:
                if not self.active_toggles.get(name, False): break
                time.sleep(0.05)
                try:
                    if action_type == "Key Input": self.inject_keys(value)
                    elif action_type == "Wait":
                        if "-" in str(value):
                            import random
                            v_min, v_max = map(float, value.split("-"))
                            time.sleep(random.uniform(v_min, v_max))
                        else:
                            time.sleep(float(value))
                    elif action_type == "Mouse Move": self.inject_mouse(value)
                except: pass
            time.sleep(0.01) # Small yield

    def execute_macro(self, macro_data):
        actions = macro_data.get("actions", [])
        repeat = macro_data.get("repeat", 1)
        for _ in range(repeat):
            for action_type, value in actions:
                time.sleep(0.05)
                try:
                    if action_type == "Key Input": self.inject_keys(value)
                    elif action_type == "Wait":
                        if "-" in str(value):
                            import random
                            v_min, v_max = map(float, value.split("-"))
                            time.sleep(random.uniform(v_min, v_max))
                        else:
                            time.sleep(float(value))
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
            elif action == "Right Click":
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 1)
                self.uinput.syn()
                time.sleep(0.05)
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 0)
                self.uinput.syn()
            elif action == "Middle Click":
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_MIDDLE, 1)
                self.uinput.syn()
                time.sleep(0.05)
                self.uinput.write(ecodes.EV_KEY, ecodes.BTN_MIDDLE, 0)
                self.uinput.syn()
        except: pass

# --- Global Engine Instance ---
engine = None

# --- GUI Application ---
class MacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Universal Linux Macro Studio")
        self.root.geometry("950x650")
        self.editing_build_name = None
        self.current_sequence = []
        
        style = ttk.Style()
        style.theme_use('clam')

        # --- TOP: Profile Selector ---
        top_frame = ttk.Frame(root, padding="10")
        top_frame.pack(fill="x")
        
        ttk.Label(top_frame, text="Active Profile:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 10))
        self.profile_var = tk.StringVar(value=engine.data.get("current_profile", "Default"))
        self.profile_selector = ttk.Combobox(top_frame, textvariable=self.profile_var, state="readonly")
        self.profile_selector.pack(side="left", padx=5)
        self.profile_selector.bind("<<ComboboxSelected>>", self.on_profile_change)
        
        ttk.Button(top_frame, text="New Profile", command=self.new_profile).pack(side="left", padx=2)
        ttk.Button(top_frame, text="Rename", command=self.rename_profile).pack(side="left", padx=2)
        ttk.Button(top_frame, text="Delete", command=self.delete_profile).pack(side="left", padx=2)

        # Main Layout: Two Columns
        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill="both", expand=True)

        # --- LEFT: Macro Builds ---
        builds_frame = ttk.Frame(paned, padding="10")
        paned.add(builds_frame, weight=1)

        ttk.Label(builds_frame, text="Macro Builds", font=("Helvetica", 12, "bold")).pack(pady=(0, 10))
        
        self.builds_list = tk.Listbox(builds_frame, font=("Helvetica", 10))
        self.builds_list.pack(fill="both", expand=True)

        build_btns = ttk.Frame(builds_frame)
        build_btns.pack(fill="x", pady=5)
        ttk.Button(build_btns, text="New", command=self.new_build).pack(side="left", padx=2)
        ttk.Button(build_btns, text="Edit", command=self.edit_build).pack(side="left", padx=2)
        ttk.Button(build_btns, text="Copy", command=self.copy_build).pack(side="left", padx=2)
        ttk.Button(build_btns, text="Delete", command=self.delete_build).pack(side="left", padx=2)
        
        import_export_btns = ttk.Frame(builds_frame)
        import_export_btns.pack(fill="x", pady=2)
        ttk.Button(import_export_btns, text="Import", command=self.import_build).pack(side="left", padx=2)
        ttk.Button(import_export_btns, text="Export", command=self.export_build).pack(side="left", padx=2)

        # --- RIGHT: Hotkey Bindings ---
        bindings_frame = ttk.Frame(paned, padding="10")
        paned.add(bindings_frame, weight=1)

        ttk.Label(bindings_frame, text="Activation Hotkeys", font=("Helvetica", 12, "bold")).pack(pady=(0, 10))
        
        self.bindings_list = tk.Listbox(bindings_frame, font=("Helvetica", 10))
        self.bindings_list.pack(fill="both", expand=True)

        bind_btns = ttk.Frame(bindings_frame)
        bind_btns.pack(fill="x", pady=5)
        ttk.Button(bind_btns, text="Bind Key", command=self.bind_hotkey).pack(side="left", padx=2)
        ttk.Button(bind_btns, text="Unbind", command=self.unbind_hotkey).pack(side="left", padx=2)

        # --- NOTIFICATION AREA ---
        self.notif_label = ttk.Label(root, text="", font=("Helvetica", 9, "italic"), foreground="gray")
        self.notif_label.pack(side="bottom", pady=5)

        # --- BOTTOM: Editor (Hidden by default) ---
        self.editor_window = None
        
        self.refresh_builds()
        self.refresh_bindings()
        self.binding_timer = None

    def refresh_builds(self):
        self.builds_list.delete(0, tk.END)
        for name in sorted(engine.data["builds"].keys()):
            self.builds_list.insert(tk.END, name)

    def refresh_bindings(self):
        self.bindings_list.delete(0, tk.END)
        current_profile = self.profile_var.get()
        bindings = engine.data["profiles"].get(current_profile, {})
        for hotkey, build_name in sorted(bindings.items()):
            self.bindings_list.insert(tk.END, f"[{hotkey}] -> {build_name}")
        
        # Update selector values
        self.profile_selector['values'] = sorted(engine.data["profiles"].keys())

    def on_profile_change(self, event):
        new_profile = self.profile_var.get()
        engine.data["current_profile"] = new_profile
        engine.save_data()
        self.refresh_bindings()
        self.notify(f"Switched to profile: {new_profile}")

    def new_profile(self):
        name = simpledialog.askstring("New Profile", "Enter name for the new profile:")
        if name:
            if name in engine.data["profiles"]:
                messagebox.showerror("Error", "Profile already exists.")
                return
            engine.data["profiles"][name] = {}
            self.profile_var.set(name)
            self.on_profile_change(None)

    def rename_profile(self):
        old_name = self.profile_var.get()
        if old_name == "Default":
            messagebox.showwarning("Warning", "Cannot rename the Default profile.")
            return
        new_name = simpledialog.askstring("Rename Profile", f"Enter new name for '{old_name}':", initialvalue=old_name)
        if new_name and new_name != old_name:
            if new_name in engine.data["profiles"]:
                messagebox.showerror("Error", "Profile name already exists.")
                return
            engine.data["profiles"][new_name] = engine.data["profiles"].pop(old_name)
            engine.data["current_profile"] = new_name
            self.profile_var.set(new_name)
            engine.save_data()
            self.refresh_bindings()

    def delete_profile(self):
        name = self.profile_var.get()
        if name == "Default":
            messagebox.showwarning("Warning", "Cannot delete the Default profile.")
            return
        if messagebox.askyesno("Confirm Delete", f"Delete profile '{name}'?"):
            del engine.data["profiles"][name]
            engine.data["current_profile"] = "Default"
            self.profile_var.set("Default")
            engine.save_data()
            self.refresh_bindings()
            self.notify(f"Deleted profile: {name}")

    def new_build(self):
        name = simpledialog.askstring("New Macro Build", "Enter a unique name for this macro build:")
        if name:
            if name in engine.data["builds"]:
                messagebox.showerror("Error", "A build with this name already exists.")
                return
            self.open_editor(name, {"actions": [], "repeat": 1})

    def edit_build(self):
        sel = self.builds_list.curselection()
        if not sel: return
        name = self.builds_list.get(sel[0])
        self.open_editor(name, engine.data["builds"][name])

    def copy_build(self):
        sel = self.builds_list.curselection()
        if not sel: return
        old_name = self.builds_list.get(sel[0])
        new_name = simpledialog.askstring("Copy Macro Build", f"Enter name for copy of '{old_name}':", initialvalue=f"{old_name}_copy")
        if new_name:
            if new_name in engine.data["builds"]:
                messagebox.showerror("Error", "A build with this name already exists.")
                return
            engine.data["builds"][new_name] = json.loads(json.dumps(engine.data["builds"][old_name]))
            engine.save_data()
            self.refresh_builds()

    def delete_build(self):
        sel = self.builds_list.curselection()
        if not sel: return
        name = self.builds_list.get(sel[0])
        if messagebox.askyesno("Confirm Delete", f"Delete macro build '{name}'?\n\nWARNING: This will also remove all hotkey bindings associated with this build across ALL profiles."):
            # Remove bindings from all profiles
            for profile_name in engine.data["profiles"]:
                bindings = engine.data["profiles"][profile_name]
                to_remove = [k for k, v in bindings.items() if v == name]
                for k in to_remove:
                    del bindings[k]
            
            del engine.data["builds"][name]
            engine.save_data()
            self.refresh_builds()
            self.refresh_bindings()
            self.notify(f"Deleted build: {name}")

    def bind_hotkey(self):
        sel = self.builds_list.curselection()
        if not sel:
            messagebox.showwarning("Selection Required", "Please select a Macro Build on the left first.")
            return
        build_name = self.builds_list.get(sel[0])
        current_profile = self.profile_var.get()
        
        # Start binding mode
        if engine.binding_mode: return
        engine.pressed_keys.clear()
        engine.binding_mode = True
        
        # Temporary UI feedback
        bind_win = tk.Toplevel(self.root)
        bind_win.title("Binding Hotkey")
        bind_win.geometry("300x150")
        ttk.Label(bind_win, text=f"Binding for: {build_name}\nProfile: {current_profile}", font=("Helvetica", 10, "bold")).pack(pady=10)
        status_label = ttk.Label(bind_win, text="Press your activation hotkey combo...")
        status_label.pack(pady=10)
        
        def finalize():
            engine.binding_mode = False
            engine.bind_callback = None
            if engine.pressed_keys:
                combo = "+".join(sorted(engine.pressed_keys))
                engine.data["profiles"][current_profile][combo] = build_name
                engine.save_data()
                self.refresh_bindings()
                self.notify(f"Bound [{combo}] to {build_name} in {current_profile}")
            bind_win.destroy()

        engine.bind_callback = lambda: self.root.after(0, finalize)
        self.root.after(5000, finalize) # Timeout

    def unbind_hotkey(self):
        sel = self.bindings_list.curselection()
        if not sel: return
        item = self.bindings_list.get(sel[0])
        hotkey = item.split("]")[0][1:]
        current_profile = self.profile_var.get()
        if messagebox.askyesno("Confirm Unbind", f"Remove binding for hotkey '{hotkey}' in profile '{current_profile}'?"):
            del engine.data["profiles"][current_profile][hotkey]
            engine.save_data()
            self.refresh_bindings()

    def notify(self, message, duration=3000):
        """Shows a temporary notification in the app."""
        self.notif_label.config(text=message)
        self.root.after(duration, lambda: self.notif_label.config(text=""))

    def export_build(self):
        sel = self.builds_list.curselection()
        if not sel: return
        name = self.builds_list.get(sel[0])
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f"{name}.json", title="Export Macro Build")
        if path:
            with open(path, "w") as f:
                json.dump({name: engine.data["builds"][name]}, f, indent=4)
            self.notify(f"Exported: {name}")

    def import_build(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Import Macro Build")
        if path:
            try:
                with open(path, "r") as f:
                    imported = json.load(f)
                    for name, build_data in imported.items():
                        new_name = name
                        if new_name in engine.data["builds"]:
                            new_name = f"{name}_imported_{int(time.time())}"
                        engine.data["builds"][new_name] = build_data
                engine.save_data()
                self.refresh_builds()
                self.notify("Imported macro build(s)")
            except Exception as e:
                messagebox.showerror("Import Error", f"Failed to import: {e}")

    # --- Editor Logic ---
    def open_editor(self, name, build_data):
        if self.editor_window: self.editor_window.destroy()
        
        self.editing_build_name = name
        self.current_sequence = list(build_data["actions"])
        
        self.editor_window = tk.Toplevel(self.root)
        self.editor_window.title(f"Editing Macro: {name}")
        self.editor_window.geometry("500x750")
        self.editor_window.transient(self.root)
        self.editor_window.grab_set()

        main_frame = ttk.Frame(self.editor_window, padding="10")
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text=f"Build Name: {name}", font=("Helvetica", 11, "bold")).pack(pady=(0, 10))

        # Mode Selection (Repeat vs Toggle)
        self.mode_frame = ttk.LabelFrame(main_frame, text=" Execution Mode ", padding=5)
        self.mode_frame.pack(fill="x", pady=5)
        
        self.mode_var = tk.StringVar(value=build_data.get("mode", "Repeat"))
        ttk.Radiobutton(self.mode_frame, text="Repeat N Times", variable=self.mode_var, value="Repeat", command=self.toggle_mode_ui).pack(side="left", padx=10)
        ttk.Radiobutton(self.mode_frame, text="Toggle On/Off (Loop until pressed again)", variable=self.mode_var, value="Toggle", command=self.toggle_mode_ui).pack(side="left", padx=10)

        # Repeat Count (Only visible if Repeat mode)
        self.rep_frame = ttk.Frame(main_frame)
        self.rep_frame.pack(fill="x", pady=5)
        ttk.Label(self.rep_frame, text="Repeat Count:").pack(side="left")
        self.edit_repeat = ttk.Spinbox(self.rep_frame, from_=1, to=999, width=10)
        self.edit_repeat.set(build_data.get("repeat", 1))
        self.edit_repeat.pack(side="left", padx=10)
        
        # Action Adder
        self.add_frame = ttk.LabelFrame(main_frame, text=" Add Action ", padding=5)
        self.add_frame.pack(fill="x", pady=10)
        
        self.toggle_mode_ui()

        self.edit_event_type = ttk.Combobox(self.add_frame, values=["Key Input", "Wait", "Mouse Move"], state="readonly")
        self.edit_event_type.set("Key Input")
        self.edit_event_type.pack(fill="x", pady=2)
        
        self.edit_options_frame = ttk.Frame(self.add_frame)
        self.edit_options_frame.pack(fill="x", pady=5)
        
        self.captured_action_combo = ""
        self.action_key_var = tk.StringVar(value="Record Keystrokes")
        
        self.edit_event_type.bind("<<ComboboxSelected>>", lambda e: self.render_edit_options())
        self.render_edit_options()

        btn_frame = ttk.Frame(self.add_frame)
        btn_frame.pack(fill="x", pady=5)
        ttk.Button(btn_frame, text="Add to Sequence", command=self.add_to_seq).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(btn_frame, text="Record Mouse", command=self.record_mouse_actions).pack(side="left", expand=True, fill="x", padx=2)

        # Sequence List
        ttk.Label(main_frame, text="Action Sequence:").pack(anchor="w")
        self.edit_seq_list = tk.Listbox(main_frame, height=10)
        self.edit_seq_list.pack(fill="both", expand=True, pady=5)
        for act in self.current_sequence:
            self.edit_seq_list.insert(tk.END, f"{act[0]}: {act[1]}")

        seq_btns = ttk.Frame(main_frame)
        seq_btns.pack(fill="x")
        ttk.Button(seq_btns, text="Move Up", command=self.move_up).pack(side="left", padx=2)
        ttk.Button(seq_btns, text="Move Down", command=self.move_down).pack(side="left", padx=2)
        ttk.Button(seq_btns, text="Remove Selected", command=self.remove_from_seq).pack(side="left", padx=2)
        ttk.Button(seq_btns, text="Clear All", command=self.clear_edit_seq).pack(side="left", padx=2)

        # Footer
        footer = ttk.Frame(main_frame)
        footer.pack(fill="x", pady=(20, 0))
        ttk.Button(footer, text="SAVE BUILD", command=self.save_build_edits).pack(side="right", padx=5)
        ttk.Button(footer, text="Cancel", command=self.editor_window.destroy).pack(side="right")

    def render_edit_options(self):
        for widget in self.edit_options_frame.winfo_children(): widget.destroy()
        etype = self.edit_event_type.get()
        
        if etype == "Key Input":
            self.action_key_btn = ttk.Button(self.edit_options_frame, textvariable=self.action_key_var, command=self.bind_edit_action_key)
            self.action_key_btn.pack(fill="x")
        elif etype == "Wait":
            ttk.Label(self.edit_options_frame, text="Seconds (e.g. 0.5 or 0.5-1.2):").pack(anchor="w")
            self.edit_wait_entry = ttk.Entry(self.edit_options_frame)
            self.edit_wait_entry.insert(0, "0.5")
            self.edit_wait_entry.pack(fill="x")
        elif etype == "Mouse Move":
            self.edit_mouse_action = ttk.Combobox(self.edit_options_frame, values=["Move by", "Left Click", "Right Click", "Middle Click"], state="readonly")
            self.edit_mouse_action.set("Move by")
            self.edit_mouse_action.pack(fill="x")
            self.edit_mouse_coords = ttk.Entry(self.edit_options_frame)
            self.edit_mouse_coords.insert(0, "100;100")
            self.edit_mouse_coords.pack(fill="x")

    def bind_edit_action_key(self):
        if engine.binding_mode: return
        
        # Cancel any existing timer to prevent "ghost" resets
        if self.binding_timer:
            self.root.after_cancel(self.binding_timer)
            self.binding_timer = None

        engine.pressed_keys.clear()
        engine.binding_mode = True
        self.action_key_var.set("Recording...")
        self.action_key_btn.config(state="disabled")
        
        def finalize():
            # Cancel timer if called manually
            if self.binding_timer:
                self.root.after_cancel(self.binding_timer)
                self.binding_timer = None
                
            engine.binding_mode = False
            engine.bind_callback = None
            
            if engine.pressed_keys:
                combo = "+".join(sorted(engine.pressed_keys))
                self.action_key_var.set(combo)
                self.captured_action_combo = combo
            else:
                self.action_key_var.set("Record Keystrokes")
            self.action_key_btn.config(state="normal")

        engine.bind_callback = lambda: self.root.after(0, finalize)
        self.binding_timer = self.root.after(5000, finalize)

    def add_to_seq(self):
        etype = self.edit_event_type.get()
        val = ""
        if etype == "Key Input":
            val = self.captured_action_combo
            if not val: return
        elif etype == "Wait":
            val = self.edit_wait_entry.get()
        elif etype == "Mouse Move":
            val = f"{self.edit_mouse_action.get()};{self.edit_mouse_coords.get()}"
        
        if val:
            self.current_sequence.append((etype, val))
            self.edit_seq_list.insert(tk.END, f"{etype}: {val}")
            if etype == "Key Input":
                self.captured_action_combo = ""
                self.action_key_var.set("Record Keystrokes")

    def remove_from_seq(self):
        sel = self.edit_seq_list.curselection()
        if sel:
            idx = sel[0]
            self.edit_seq_list.delete(idx)
            self.current_sequence.pop(idx)

    def clear_edit_seq(self):
        self.current_sequence = []
        self.edit_seq_list.delete(0, tk.END)

    def record_mouse_actions(self):
        mice = engine.find_mice()
        if not mice:
            messagebox.showerror("Error", "No mouse devices found.")
            return
        
        rec_win = tk.Toplevel(self.editor_window)
        rec_win.title("Recording Mouse...")
        rec_win.geometry("300x100")
        rec_win.transient(self.editor_window)
        rec_win.grab_set()
        
        ttk.Label(rec_win, text="Recording mouse movements and clicks...\nClick 'Stop' when finished.").pack(pady=10)
        
        recorded_actions = []
        is_recording = [True]
        
        def stop_rec():
            is_recording[0] = False
            rec_win.destroy()
            for act in recorded_actions:
                self.current_sequence.append(act)
                self.edit_seq_list.insert(tk.END, f"{act[0]}: {act[1]}")

        ttk.Button(rec_win, text="Stop Recording", command=stop_rec).pack()

        def rec_thread():
            import select
            fds = {m.fd: m for m in mice}
            last_time = time.time()
            
            # Accumulate movement to avoid too many small actions
            acc_x = 0
            acc_y = 0
            
            while is_recording[0]:
                r, _, _ = select.select(fds, [], [], 0.1)
                for fd in r:
                    for event in fds[fd].read():
                        if not is_recording[0]: break
                        
                        if event.type == ecodes.EV_REL:
                            if event.code == ecodes.REL_X: acc_x += event.value
                            elif event.code == ecodes.REL_Y: acc_y += event.value
                            
                            # Record movement every 50ms if there's significant movement
                            curr_time = time.time()
                            if curr_time - last_time > 0.05:
                                if acc_x != 0 or acc_y != 0:
                                    recorded_actions.append(("Mouse Move", f"Move by;{acc_x};{acc_y}"))
                                    acc_x = 0
                                    acc_y = 0
                                    last_time = curr_time
                                    
                        elif event.type == ecodes.EV_KEY:
                            if event.value == 1: # Button Down
                                if event.code == ecodes.BTN_LEFT:
                                    recorded_actions.append(("Mouse Move", "Left Click;"))
                                elif event.code == ecodes.BTN_RIGHT:
                                    recorded_actions.append(("Mouse Move", "Right Click;"))
                                elif event.code == ecodes.BTN_MIDDLE:
                                    recorded_actions.append(("Mouse Move", "Middle Click;"))

        threading.Thread(target=rec_thread, daemon=True).start()

    def move_up(self):
        sel = self.edit_seq_list.curselection()
        if sel:
            idx = sel[0]
            if idx > 0:
                item = self.current_sequence.pop(idx)
                self.current_sequence.insert(idx - 1, item)
                self.edit_seq_list.delete(idx)
                self.edit_seq_list.insert(idx - 1, f"{item[0]}: {item[1]}")
                self.edit_seq_list.selection_set(idx - 1)

    def move_down(self):
        sel = self.edit_seq_list.curselection()
        if sel:
            idx = sel[0]
            if idx < len(self.current_sequence) - 1:
                item = self.current_sequence.pop(idx)
                self.current_sequence.insert(idx + 1, item)
                self.edit_seq_list.delete(idx)
                self.edit_seq_list.insert(idx + 1, f"{item[0]}: {item[1]}")
                self.edit_seq_list.selection_set(idx + 1)

    def toggle_mode_ui(self):
        if self.mode_var.get() == "Repeat":
            self.rep_frame.pack(fill="x", pady=5, after=self.mode_frame)
        else:
            self.rep_frame.pack_forget()

    def save_build_edits(self):
        if not self.current_sequence:
            messagebox.showwarning("Empty Sequence", "Please add at least one action.")
            return
        
        engine.data["builds"][self.editing_build_name] = {
            "actions": self.current_sequence,
            "repeat": int(self.edit_repeat.get()),
            "mode": self.mode_var.get()
        }
        engine.save_data()
        self.refresh_builds()
        self.editor_window.destroy()

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
