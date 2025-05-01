#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlite_viewer.py

A minimal, single-file, zero-dependency SQLite browser using Python's
standard library (tkinter and sqlite3).

Version: 1.2.1

Features:
- Open/Close SQLite database files (.sqlite, .db, .sqlite3).
- Remembers window size/position and recent files.
- Left pane: Schema browser (Tables/Views -> Columns(types/PK)/Indices).
- Top-right pane: SQL editor (Ctrl+Enter exec, configurable font).
- Bottom-right pane: Query results viewer (tabular display, searchable, copyable).
- Status bar: Shows current DB, operation status, query time.
- Query History (session-based).
- Basic SQL Templates via schema context menu.
- Configurable via '.sqlite_viewer.ini' in user's home directory.
"""

import os
import sys
import sqlite3
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, font, simpledialog
from tkinter import ttk
import traceback # For debugging output
import configparser
import time
import platform
from collections import deque # For history/recent files

# --- Constants ---
APP_TITLE = "Minimal SQLite Viewer"
APP_VERSION = "1.2.1" # Incremented version for fix
CONFIG_FILENAME = ".sqlite_viewer.ini"
DEFAULT_WINDOW_WIDTH = 900
DEFAULT_WINDOW_HEIGHT = 700
WINDOW_MIN_WIDTH = 600
WINDOW_MIN_HEIGHT = 400
DEFAULT_SQL_EDITOR_FONT_FAMILY = "Courier"
DEFAULT_SQL_EDITOR_FONT_SIZE = 10
SQL_PROMPT = "-- Write your SQL here. Run with Ctrl+Enter"
STATUS_BAR_MAX_PATH_LEN = 70
NULL_DISPLAY_TEXT = "*NULL*"
BLOB_DISPLAY_TEXT_FORMAT = "<BLOB ({} bytes)>"
ROW_COLOR_ODD = 'white'
ROW_COLOR_EVEN = '#f0f0f0'
MAX_RECENT_FILES = 10
MAX_QUERY_HISTORY = 20

# --- Helper Functions ---
def get_config_path():
    """Gets the platform-specific path for the config file."""
    return os.path.join(os.path.expanduser("~"), CONFIG_FILENAME)

def format_blob_size(data_bytes):
    """Formats BLOB size for display."""
    size = len(data_bytes)
    return BLOB_DISPLAY_TEXT_FORMAT.format(size)

# --- Main Application Class ---
class SQLiteViewer(tk.Tk):
    """Main application window for the SQLite Viewer."""

    def __init__(self):
        super().__init__()

        # --- Configuration Loading ---
        # Use self.app_config to avoid clashing with widget's .config() method
        self.app_config = configparser.ConfigParser()
        self._load_config() # This method now uses self.app_config

        # --- Window Setup ---
        self.title(APP_TITLE)
        initial_geom = self.app_config.get("Window", "geometry", fallback=None) # Use app_config
        if initial_geom:
            try:
                self.geometry(initial_geom)
            except tk.TclError:
                print("Warning: Invalid saved window geometry, using default.", file=sys.stderr)
                self._set_default_geometry()
        else:
            self._set_default_geometry()

        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- State Variables ---
        self.db_path = None
        self.conn = None
        self.full_query_rows = []
        self.current_columns = []
        self.recent_files = deque(self._get_list_from_config("RecentFiles", "paths"), maxlen=MAX_RECENT_FILES) # Uses app_config
        self.query_history = deque(maxlen=MAX_QUERY_HISTORY)

        # --- Theming and Fonts ---
        style = ttk.Style(self)
        style.map("Treeview", background=[('selected', style.lookup("Treeview", "background"))])
        style.configure("Treeview", rowheight=20)
        style.configure("oddrow.Treeview", background=ROW_COLOR_ODD)
        style.configure("evenrow.Treeview", background=ROW_COLOR_EVEN)
        style.configure("info.Treeview", foreground="black")
        style.configure("error.Treeview", foreground="red")

        # Load SQL editor font from app_config or use defaults
        self.sql_font_family = self.app_config.get("Editor", "font_family", fallback=DEFAULT_SQL_EDITOR_FONT_FAMILY) # Use app_config
        try:
            self.sql_font_size = self.app_config.getint("Editor", "font_size", fallback=DEFAULT_SQL_EDITOR_FONT_SIZE) # Use app_config
        except ValueError:
            self.sql_font_size = DEFAULT_SQL_EDITOR_FONT_SIZE
        try:
            self.sql_editor_font = font.Font(family=self.sql_font_family, size=self.sql_font_size)
        except tk.TclError:
            print(f"Warning: Font '{self.sql_font_family}' not found, using default monospace.", file=sys.stderr)
            self.sql_editor_font = font.Font(family="Courier", size=DEFAULT_SQL_EDITOR_FONT_SIZE)

        # --- UI Creation ---
        self._create_menu()
        self._create_ui()
        self._create_bindings()
        self._update_status("No database loaded.")
        self._update_menu_state()
        self._update_recent_files_menu()

    def _set_default_geometry(self):
        """Sets the window to default size and centers it."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x_coord = int((screen_width / 2) - (DEFAULT_WINDOW_WIDTH / 2))
        y_coord = int((screen_height / 2) - (DEFAULT_WINDOW_HEIGHT / 2))
        self.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}+{x_coord}+{y_coord}")

    # ----- Configuration Methods -----

    def _load_config(self):
        """Loads configuration from the INI file using self.app_config."""
        config_path = get_config_path()
        if os.path.exists(config_path):
            try:
                self.app_config.read(config_path) # Read into app_config
            except configparser.Error as e:
                print(f"Warning: Error reading config file {config_path}: {e}", file=sys.stderr)
        # Ensure sections exist even if file was empty or new
        if not self.app_config.has_section("Window"): self.app_config.add_section("Window")
        if not self.app_config.has_section("RecentFiles"): self.app_config.add_section("RecentFiles")
        if not self.app_config.has_section("Editor"): self.app_config.add_section("Editor")

    def _save_config(self):
        """Saves current configuration to the INI file using self.app_config."""
        try:
            # Update app_config object with current state
            self.app_config.set("Window", "geometry", self.geometry())
            self.app_config.set("RecentFiles", "paths", ",".join(self.recent_files))
            self.app_config.set("Editor", "font_family", self.sql_font_family)
            self.app_config.set("Editor", "font_size", str(self.sql_font_size))

            config_path = get_config_path()
            with open(config_path, 'w') as configfile:
                self.app_config.write(configfile) # Write from app_config
        except (configparser.Error, IOError, OSError) as e:
            print(f"Warning: Could not save config file {get_config_path()}: {e}", file=sys.stderr)

    def _get_list_from_config(self, section, key):
        """Safely retrieves a comma-separated list from self.app_config."""
        value = self.app_config.get(section, key, fallback="") # Get from app_config
        return [item.strip() for item in value.split(',') if item.strip()]

    def _on_close(self):
        """Handles window closing: saves config and destroys window."""
        self._save_config() # This method now uses self.app_config
        self.destroy()

    # ----- UI Setup Methods -----

    def _create_menu(self):
        """Creates the main application menu bar."""
        self.menu_bar = tk.Menu(self) # Create the menu bar itself

        # --- File Menu ---
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="Open Database...", command=self._open_db, accelerator="Ctrl+O")
        self.recent_files_menu = tk.Menu(self.file_menu, tearoff=False)
        self.file_menu.add_cascade(label="Open Recent", menu=self.recent_files_menu, state=tk.DISABLED)
        self.file_menu.add_command(label="Close Database", command=self._close_db, accelerator="Ctrl+W", state=tk.DISABLED)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Export Results as CSV...", command=self._export_csv, state=tk.DISABLED)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self._on_close, accelerator="Ctrl+Q")
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)

        # --- Edit Menu ---
        self.edit_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.query_history_menu = tk.Menu(self.edit_menu, tearoff=False)
        self.edit_menu.add_cascade(label="Query History", menu=self.query_history_menu, state=tk.DISABLED)
        self.menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        # --- View Menu ---
        self.view_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.view_menu.add_command(label="Refresh Schema", command=self._refresh_schema, accelerator="F5", state=tk.DISABLED)
        self.menu_bar.add_cascade(label="View", menu=self.view_menu)

        # --- Help Menu ---
        help_menu = tk.Menu(self.menu_bar, tearoff=False)
        help_menu.add_command(label="About", command=self._show_about)
        self.menu_bar.add_cascade(label="Help", menu=help_menu)

        # Configure the main window (self) to use the created menu bar
        # This uses the Tkinter widget's config method, NOT the ConfigParser instance
        self.config(menu=self.menu_bar) # <--- THIS IS THE CORRECT USAGE NOW

    def _create_ui(self):
        """Creates the main UI layout with three panes."""
        # Main PanedWindow: Left (Schema) | Right (Editor/Results)
        main_paned_window = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- Left Pane: Schema Browser ---
        frame_schema = ttk.Frame(main_paned_window, width=250)
        main_paned_window.add(frame_schema, weight=1)

        lbl_schema = ttk.Label(frame_schema, text="Schema (Tables / Views)", anchor="w")
        lbl_schema.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5, 0))

        self.tree_schema = ttk.Treeview(frame_schema, show="tree", selectmode="browse")
        self.tree_schema.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree_schema.bind("<<TreeviewOpen>>", self._on_schema_expand)
        if platform.system() == "Darwin": # macOS
            self.tree_schema.bind("<Button-2>", self._show_schema_context_menu)
        else: # Windows/Linux
            self.tree_schema.bind("<Button-3>", self._show_schema_context_menu)

        schema_scrollbar = ttk.Scrollbar(self.tree_schema, orient="vertical", command=self.tree_schema.yview)
        self.tree_schema.configure(yscrollcommand=schema_scrollbar.set)
        schema_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # --- Right Pane: Editor / Results (Vertically Paned) ---
        right_paned_window = ttk.Panedwindow(main_paned_window, orient=tk.VERTICAL)
        main_paned_window.add(right_paned_window, weight=3)

        # --- Top-Right Pane: SQL Editor ---
        frame_editor = ttk.Frame(right_paned_window)
        right_paned_window.add(frame_editor, weight=2)

        lbl_editor = ttk.Label(frame_editor, text="SQL Editor", anchor="w")
        lbl_editor.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5, 0))

        editor_text_frame = ttk.Frame(frame_editor)
        editor_text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        self.txt_sql = tk.Text(editor_text_frame, wrap="none", height=10,
                               font=self.sql_editor_font, undo=True, maxundo=-1)
        self.txt_sql.insert("1.0", SQL_PROMPT)
        self.txt_sql.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sql_scroll_y = ttk.Scrollbar(editor_text_frame, orient=tk.VERTICAL, command=self.txt_sql.yview)
        sql_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_sql['yscrollcommand'] = sql_scroll_y.set

        sql_scroll_x = ttk.Scrollbar(frame_editor, orient=tk.HORIZONTAL, command=self.txt_sql.xview)
        sql_scroll_x.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        self.txt_sql['xscrollcommand'] = sql_scroll_x.set

        editor_button_frame = ttk.Frame(frame_editor)
        editor_button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=3)
        ttk.Button(editor_button_frame, text="Run Query (Ctrl+Enter)", command=self._run_query).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(editor_button_frame, text="Clear", command=self._clear_editor).pack(side=tk.LEFT)

        # --- Bottom-Right Pane: Results Viewer ---
        frame_results = ttk.Frame(right_paned_window)
        right_paned_window.add(frame_results, weight=3)

        search_frame = ttk.Frame(frame_results)
        search_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5,0))
        ttk.Label(search_frame, text="Filter Results:").pack(side=tk.LEFT, padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_var.trace_add("write", self._filter_results)
        self.search_entry.configure(state=tk.DISABLED)

        results_tree_frame = ttk.Frame(frame_results)
        results_tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0,5))

        self.tree_res = ttk.Treeview(results_tree_frame, show="headings", selectmode="extended")
        self.tree_res.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.results_context_menu = tk.Menu(self.tree_res, tearoff=0)
        self.results_context_menu.add_command(label="Copy Selected Cell(s)", command=self._copy_selection_to_clipboard)
        self.results_context_menu.add_command(label="Copy Selected Row(s)", command=lambda: self._copy_selection_to_clipboard(copy_rows=True))

        if platform.system() == "Darwin": # macOS
            self.tree_res.bind("<Button-2>", self._show_results_context_menu)
            self.tree_res.bind("<Command-c>", lambda e: self._copy_selection_to_clipboard())
        else: # Windows/Linux
            self.tree_res.bind("<Button-3>", self._show_results_context_menu)
            self.tree_res.bind("<Control-c>", lambda e: self._copy_selection_to_clipboard())

        res_scroll_y = ttk.Scrollbar(results_tree_frame, orient=tk.VERTICAL, command=self.tree_res.yview)
        res_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        res_scroll_x = ttk.Scrollbar(frame_results, orient=tk.HORIZONTAL, command=self.tree_res.xview)
        res_scroll_x.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        self.tree_res.configure(yscrollcommand=res_scroll_y.set, xscrollcommand=res_scroll_x.set)

        # --- Status Bar ---
        self.status_var = tk.StringVar()
        status_bar = ttk.Label(self, textvariable=self.status_var, anchor=tk.W, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=0, pady=0)

    def _create_bindings(self):
        """Creates global keyboard shortcuts."""
        self.bind_all("<Control-o>", lambda e: self._open_db())
        self.bind_all("<Control-w>", lambda e: self._close_db() if self.conn else None)
        self.bind_all("<Control-q>", lambda e: self._on_close())
        self.bind_all("<F5>", lambda e: self._refresh_schema() if self.conn else None)

        self.txt_sql.bind("<Control-Return>", lambda e: (self._run_query(), "break"))

    # ----- Dynamic Menu Updates -----

    def _update_recent_files_menu(self):
        """Updates the 'Open Recent' submenu."""
        self.recent_files_menu.delete(0, tk.END)
        if not self.recent_files:
            self.recent_files_menu.add_command(label="(No recent files)", state=tk.DISABLED)
            self.file_menu.entryconfigure("Open Recent", state=tk.DISABLED)
        else:
            for i, path in enumerate(reversed(self.recent_files)):
                self.recent_files_menu.add_command(label=f"{i+1}. {self._truncate_path(path, 50)}",
                                                   command=lambda p=path: self._open_recent_file(p))
            self.file_menu.entryconfigure("Open Recent", state=tk.NORMAL)

    def _update_query_history_menu(self):
        """Updates the 'Query History' submenu."""
        self.query_history_menu.delete(0, tk.END)
        if not self.query_history:
            self.query_history_menu.add_command(label="(No history)", state=tk.DISABLED)
            self.edit_menu.entryconfigure("Query History", state=tk.DISABLED)
        else:
            for i, sql in enumerate(reversed(self.query_history)):
                display_sql = (sql[:70] + '...') if len(sql) > 70 else sql
                display_sql = display_sql.replace('\n', ' ')
                self.query_history_menu.add_command(label=f"{i+1}. {display_sql}",
                                                    command=lambda q=sql: self._load_query_from_history(q))
            self.edit_menu.entryconfigure("Query History", state=tk.NORMAL)

    def _update_menu_state(self):
        """Enables/disables menu items based on application state."""
        db_is_open = self.conn is not None
        results_exist = bool(self.full_query_rows and self.current_columns)

        self.file_menu.entryconfigure("Close Database", state=tk.NORMAL if db_is_open else tk.DISABLED)
        self.file_menu.entryconfigure("Export Results as CSV...", state=tk.NORMAL if db_is_open and results_exist else tk.DISABLED)

        self.edit_menu.entryconfigure("Query History", state=tk.NORMAL if self.query_history else tk.DISABLED)

        self.view_menu.entryconfigure("Refresh Schema", state=tk.NORMAL if db_is_open else tk.DISABLED)

        self.search_entry.configure(state=tk.NORMAL if results_exist else tk.DISABLED)
        if not results_exist:
            self.search_var.set("")

    # ----- Action Methods -----

    def _add_to_recent_files(self, path):
        """Adds a path to recent files, avoiding duplicates and managing size."""
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.appendleft(path)
        self._update_recent_files_menu()

    def _open_recent_file(self, path):
        """Opens a database from the recent files list."""
        if not os.path.exists(path):
            messagebox.showerror("File Not Found", f"The file could not be found:\n{path}")
            try:
                self.recent_files.remove(path)
                self._update_recent_files_menu()
            except ValueError: pass
            return
        self._open_db_internal(path)

    def _open_db(self, event=None):
        """Shows dialog to open a SQLite database file."""
        path = filedialog.askopenfilename(
            title="Open SQLite Database",
            filetypes=[("SQLite files", "*.sqlite *.db *.sqlite3"), ("All files", "*.*")]
        )
        if path:
            self._open_db_internal(path)

    def _open_db_internal(self, path):
        """Internal logic to open a specific database path."""
        self._set_busy_cursor()
        try:
            if self.conn:
                self._close_db_internal()

            new_conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
            new_conn.execute("PRAGMA quick_check;").fetchone()

            self.db_path = path
            self.conn = new_conn
            self._add_to_recent_files(path)
            self._update_status(f"Connected: {self._truncate_path(path)}")
            self._refresh_schema()
            self.txt_sql.delete("1.0", tk.END)
            self.txt_sql.insert("1.0", SQL_PROMPT)
            self._clear_results()

        except sqlite3.Error as ex:
            self._close_db_internal()
            messagebox.showerror("Database Error", f"Failed to open or validate database:\n{path}\n\nError: {ex}")
            self._update_status("Failed to open database.")
        except Exception as ex:
            self._close_db_internal()
            messagebox.showerror("Error", f"An unexpected error occurred opening file:\n{path}\n\nError: {ex}")
            print(f"Unexpected error opening DB: {traceback.format_exc()}", file=sys.stderr)
            self._update_status("Error opening file.")
        finally:
            self._reset_cursor()
            self._update_menu_state()

    def _close_db(self, event=None):
        """Closes the currently open database and resets the UI."""
        self._close_db_internal()
        self._update_status("No database loaded.")
        self._update_menu_state()

    def _close_db_internal(self):
        """Internal helper to close connection and clear UI state."""
        if self.conn:
            try:
                self.conn.close()
            except Exception as e:
                print(f"Warning: Error closing database: {e}", file=sys.stderr)
        self.conn = None
        self.db_path = None
        for item in self.tree_schema.get_children(): self.tree_schema.delete(item)
        self._clear_results()

    def _refresh_schema(self, event=None):
        """Refreshes the schema view (tables, views, indices)."""
        if not self.conn: return
        self._set_busy_cursor()
        try:
            for item in self.tree_schema.get_children(): self.tree_schema.delete(item)

            cursor = self.conn.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name;"
            )
            items = cursor.fetchall()

            if not items:
                self.tree_schema.insert("", "end", text="No tables or views found", open=False, tags=('info',))
            else:
                for name, type_, sql in items:
                    display_text = f"{name} ({type_})"
                    iid = self.tree_schema.insert("", "end", text=display_text, values=(name, type_, sql), open=False)
                    self.tree_schema.insert(iid, "end", text="loading...")

            self._update_status("Schema refreshed.")

        except Exception as ex:
            messagebox.showerror("Schema Error", f"Failed to retrieve schema information.\n\nError: {ex}")
            self._update_status("Error refreshing schema.")
            print(f"Unexpected error refreshing schema: {traceback.format_exc()}", file=sys.stderr)
        finally:
            self._reset_cursor()

    def _on_schema_expand(self, event):
        """Loads columns and indices when a table/view node is expanded."""
        item_id = self.tree_schema.focus()
        if not item_id: return

        children = self.tree_schema.get_children(item_id)
        if not children or self.tree_schema.item(children[0], "text") != "loading...":
            return

        self.tree_schema.delete(children[0])

        item_values = self.tree_schema.item(item_id, "values")
        if not item_values: return
        name, type_, sql = item_values
        safe_name = f'"{name.replace("\"", "\"\"")}"'

        self._set_busy_cursor()
        try:
            cursor_cols = self.conn.execute(f"PRAGMA table_info({safe_name});")
            columns = cursor_cols.fetchall()
            if columns:
                col_node_id = self.tree_schema.insert(item_id, "end", text="Columns", open=True)
                for cid, cname, ctype, notnull, dflt, pk in columns:
                    ctype_disp = ctype.upper() if ctype else 'UNKNOWN'
                    pk_disp = " (PK)" if pk == 1 else ""
                    col_display = f"{cname} ({ctype_disp}){pk_disp}"
                    # Store type and name for potential future use (e.g. better templates)
                    self.tree_schema.insert(col_node_id, "end", text=col_display, values=("column", cname))
            else:
                 self.tree_schema.insert(item_id, "end", text="No columns found", open=False, tags=('info',))

            if type_ == 'table':
                cursor_idx = self.conn.execute(f"PRAGMA index_list({safe_name});")
                indices = cursor_idx.fetchall()
                if indices:
                    idx_node_id = self.tree_schema.insert(item_id, "end", text="Indices", open=False)
                    for seq, idx_name, unique, origin, partial in indices:
                         self.tree_schema.insert(idx_node_id, "end", text=idx_name, values=("index", idx_name))

        except Exception as ex:
            messagebox.showerror("Schema Error", f"Failed to retrieve details for '{name}'.\n\nError: {ex}")
            self.tree_schema.insert(item_id, "end", text="Error loading details", open=False, tags=('error',))
            print(f"Unexpected error expanding schema item: {traceback.format_exc()}", file=sys.stderr)
        finally:
            self._reset_cursor()

    def _show_schema_context_menu(self, event):
        """Displays a context menu for schema items."""
        item_id = self.tree_schema.identify_row(event.y)
        if not item_id: return
        self.tree_schema.focus(item_id)

        item_info = self.tree_schema.item(item_id) # Get full item info
        item_values = item_info.get("values")

        # Check if it's a top-level table/view item or a sub-item
        parent_id = self.tree_schema.parent(item_id)
        is_top_level = not parent_id # Top level items have "" as parent

        menu = tk.Menu(self, tearoff=0)

        if is_top_level and item_values and item_values[0] in ('table', 'view'):
            # It's a table or view node
            table_name = item_values[0]
            menu.add_command(label=f"SELECT * FROM {table_name}",
                             command=lambda n=table_name: self._insert_sql_template('select', n))
            menu.add_command(label=f"SELECT COUNT(*) FROM {table_name}",
                             command=lambda n=table_name: self._insert_sql_template('count', n))
            if item_values[1] == 'table': # Check type stored in values[1]
                 menu.add_command(label=f"INSERT INTO {table_name} ...",
                                  command=lambda n=table_name: self._insert_sql_template('insert', n))
            menu.add_separator()
            menu.add_command(label=f"PRAGMA table_info({table_name})",
                             command=lambda n=table_name: self._insert_sql_template('pragma_info', n))
        elif item_values and len(item_values) > 1:
             # It's likely a sub-item (column or index)
             item_type = item_values[0]
             item_name = item_values[1]
             if item_type == 'index':
                 menu.add_command(label=f"PRAGMA index_info({item_name})",
                                  command=lambda n=item_name: self._insert_sql_template('pragma_idx_info', n))
             # Add more context actions for columns if desired

        if menu.index(tk.END) is not None:
            menu.tk_popup(event.x_root, event.y_root)


    def _insert_sql_template(self, template_type, name):
        """Inserts a SQL template into the editor."""
        sql = ""
        safe_name = f'"{name.replace("\"", "\"\"")}"'

        if template_type == 'select':       sql = f"SELECT * FROM {safe_name};"
        elif template_type == 'count':      sql = f"SELECT COUNT(*) FROM {safe_name};"
        elif template_type == 'insert':     sql = f"INSERT INTO {safe_name} (column1, column2) VALUES (?, ?);"
        elif template_type == 'pragma_info':sql = f"PRAGMA table_info({safe_name});"
        elif template_type == 'pragma_idx_info': sql = f"PRAGMA index_info({safe_name});" # Name is index name

        if sql:
            self.txt_sql.delete("1.0", tk.END)
            self.txt_sql.insert("1.0", sql + "\n")
            self.txt_sql.focus_set()

    def _run_query(self, event=None):
        """Executes the SQL query entered in the editor."""
        if not self.conn:
            messagebox.showwarning("No Database", "Please open a database file first.")
            return

        sql = self.txt_sql.get("1.0", tk.END).strip()
        if not sql or sql == SQL_PROMPT:
            self._update_status("Enter SQL query to run.")
            return

        self._set_busy_cursor()
        self._clear_results()
        self.search_var.set("")

        start_time = time.monotonic()
        try:
            is_select_query = sql.lstrip().upper().startswith("SELECT")
            cursor = self.conn.cursor()

            if is_select_query:
                cursor.execute(sql)
                rows = cursor.fetchall()
                self.full_query_rows = rows
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    self._display_results(columns, rows)
                    elapsed = time.monotonic() - start_time
                    self._update_status(f"Query executed ({elapsed:.3f}s). {len(rows)} row(s) returned.")
                else:
                    self.conn.commit()
                    self._display_message_in_results("Statement executed (no standard columns returned).")
                    elapsed = time.monotonic() - start_time
                    self._update_status(f"Statement executed ({elapsed:.3f}s).")
            else:
                cursor.execute(sql)
                rowcount = cursor.rowcount
                self.conn.commit()
                elapsed = time.monotonic() - start_time
                if rowcount != -1:
                    self._display_message_in_results(f"Statement executed successfully. {rowcount} row(s) affected.")
                    self._update_status(f"Statement executed ({elapsed:.3f}s). {rowcount} row(s) affected.")
                else:
                    self._display_message_in_results("Statement executed successfully.")
                    self._update_status(f"Statement executed ({elapsed:.3f}s).")

            if sql not in self.query_history:
                self.query_history.appendleft(sql)
            self._update_query_history_menu()

        except sqlite3.Error as ex:
            try: self.conn.rollback()
            except Exception: pass
            elapsed = time.monotonic() - start_time
            error_message = f"SQL Error: {ex}"
            self._display_message_in_results(error_message, is_error=True)
            self._update_status(f"Error executing query ({elapsed:.3f}s).")
        except Exception as ex:
            try: self.conn.rollback()
            except Exception: pass
            elapsed = time.monotonic() - start_time
            error_message = f"Unexpected Error: {ex}"
            self._display_message_in_results(error_message, is_error=True)
            print(f"Unexpected error executing query: {traceback.format_exc()}", file=sys.stderr)
            self._update_status(f"Error executing query ({elapsed:.3f}s).")
        finally:
            self._reset_cursor()
            self._update_menu_state()

    def _load_query_from_history(self, sql):
        """Loads a query from history into the editor."""
        self.txt_sql.delete("1.0", tk.END)
        self.txt_sql.insert("1.0", sql)
        self.txt_sql.focus_set()

    def _clear_editor(self, event=None):
        """Clears the SQL editor."""
        self.txt_sql.delete("1.0", tk.END)

    def _display_results(self, columns, rows_to_display):
        """Displays query results (potentially filtered) in the results Treeview."""
        self._clear_results_treeview_only()
        self.current_columns = columns

        if not columns:
             self._display_message_in_results("Query executed, but returned no columns.")
             return

        self.tree_res["columns"] = columns
        for col_name in columns:
            self.tree_res.heading(col_name, text=col_name, anchor=tk.W)
            self.tree_res.column(col_name, minwidth=40, stretch=True, anchor=tk.W)

        if not rows_to_display:
            if self.search_var.get():
                self._display_message_in_results("No results match filter.", is_error=False)
            else:
                self._display_message_in_results("Query returned no results.", is_error=False)
        else:
            for i, row in enumerate(rows_to_display):
                display_values = []
                for val in row:
                    if val is None:         display_values.append(NULL_DISPLAY_TEXT)
                    elif isinstance(val, bytes): display_values.append(format_blob_size(val))
                    else:                   display_values.append(str(val))
                tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                self.tree_res.insert("", "end", values=display_values, tags=(tag,))

    def _filter_results(self, *args):
        """Filters the displayed results based on the search entry."""
        if not self.conn or not self.current_columns:
            return
        search_term = self.search_var.get().lower()
        if not search_term:
            self._display_results(self.current_columns, self.full_query_rows)
            self._update_status(f"{len(self.full_query_rows)} row(s) displayed.")
            return

        filtered_rows = []
        try:
            for row in self.full_query_rows:
                match = False
                for val in row:
                    if search_term in str(val).lower():
                        match = True
                        break
                if match:
                    filtered_rows.append(row)
            self._display_results(self.current_columns, filtered_rows)
            self._update_status(f"{len(filtered_rows)} row(s) match filter '{search_term}'.")
        except Exception as e:
             print(f"Error during filtering: {e}", file=sys.stderr)
             self._update_status("Error applying filter.")

    def _display_message_in_results(self, message, is_error=False):
        """Displays a textual message (info or error) in the results area."""
        self._clear_results_treeview_only()
        self.current_columns = []
        self.tree_res["columns"] = ("Message",)
        self.tree_res.heading("Message", text="Status")
        self.tree_res.column("Message", anchor=tk.W, stretch=tk.YES)
        tag = 'error' if is_error else 'info'
        # Tag configuration moved to __init__ where style is available
        self.tree_res.insert("", "end", values=(message,), tags=(tag,))

    def _clear_results_treeview_only(self):
        """Clears only the Treeview widget, preserving internal data."""
        if self.tree_res.winfo_exists(): # Check if widget exists before deleting children
            self.tree_res.delete(*self.tree_res.get_children())
            self.tree_res["columns"] = ()

    def _clear_results(self):
        """Clears the results Treeview AND internal data."""
        self._clear_results_treeview_only()
        self.full_query_rows = []
        self.current_columns = []
        self.search_var.set("")

    def _show_results_context_menu(self, event):
        """Shows the context menu for the results Treeview."""
        if self.tree_res.identify_row(event.y) and self.current_columns:
             selection = self.tree_res.selection()
             menu_state = tk.NORMAL if selection else tk.DISABLED
             self.results_context_menu.entryconfigure("Copy Selected Cell(s)", state=menu_state)
             self.results_context_menu.entryconfigure("Copy Selected Row(s)", state=menu_state)
             self.results_context_menu.tk_popup(event.x_root, event.y_root)

    def _copy_selection_to_clipboard(self, copy_rows=False):
        """Copies selected Treeview data (cells or rows) to clipboard as TSV."""
        selection = self.tree_res.selection()
        if not selection: return

        clipboard_content = []
        if copy_rows:
            for item_id in selection:
                values = self.tree_res.item(item_id, 'values')
                clipboard_content.append("\t".join(map(str, values)))
        else:
            # Copy focused cell or first selected cell's value
            focused_item = self.tree_res.focus()
            target_item = focused_item if focused_item in selection else (selection[0] if selection else None)

            if target_item:
                 try:
                     # Identify column based on pointer position *relative to the Treeview widget*
                     x = event.x # Assumes event is passed or available; Need to bind differently
                     # Let's rethink this - use focus cell identification if possible
                     # Tkinter Treeview doesn't easily give the focused *cell*.
                     # Simplification: Copy the value from the first column of the focused/first selected row.
                     values = self.tree_res.item(target_item, 'values')
                     if values:
                         clipboard_content.append(str(values[0])) # Copy only the first column value
                 except Exception as e:
                     print(f"Debug: Error identifying cell for copy: {e}")
                     # Fallback: Copy the whole first selected row if cell identification fails
                     if selection:
                        values = self.tree_res.item(selection[0], 'values')
                        clipboard_content.append("\t".join(map(str, values)))


        if clipboard_content:
            try:
                self.clipboard_clear()
                self.clipboard_append("\n".join(clipboard_content))
                self._update_status(f"Data copied to clipboard.") # Simplified message
            except tk.TclError:
                self._update_status("Error: Could not access clipboard.")

    def _export_csv(self, event=None):
        """Exports the current full results (pre-filter) to a CSV file."""
        if not self.conn or not self.current_columns or not self.full_query_rows:
            messagebox.showwarning("Export Error", "No results available to export.")
            return

        filepath = filedialog.asksaveasfilename(
            title="Export Results as CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="query_results.csv"
        )
        if not filepath: return

        self._set_busy_cursor()
        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(self.current_columns)
                for row in self.full_query_rows:
                    formatted_row = []
                    for val in row:
                        if val is None: formatted_row.append('')
                        elif isinstance(val, bytes): formatted_row.append(format_blob_size(val))
                        else: formatted_row.append(val)
                    writer.writerow(formatted_row)
            self._update_status(f"Results exported to {os.path.basename(filepath)}")

        except Exception as ex:
             messagebox.showerror("Export Error", f"Failed to write CSV file:\n{filepath}\n\nError: {ex}")
             self._update_status("Error exporting results.")
             print(f"Unexpected error exporting CSV: {traceback.format_exc()}", file=sys.stderr)
        finally:
             self._reset_cursor()

    def _show_about(self):
        """Displays the About dialog."""
        messagebox.showinfo(APP_TITLE,
            f"{APP_TITLE} - Version {APP_VERSION}\n\n"
            "A simple, single-file SQLite database viewer.\n"
            "Built with Python's standard library.\n\n"
            "Features:\n"
            "- Schema browsing (Tables/Views/Columns/Indices)\n"
            "- SQL Editor with History & Templates\n"
            "- Results Viewer with Filtering & CSV Export\n"
            "- Remembers Window State & Recent Files\n\n"
            "(c) 2024 - MIT License (Assumed)"
        )

    # ----- Helper Methods -----

    def _update_status(self, text):
        """Updates the status bar text."""
        self.status_var.set(str(text)[:250])

    def _truncate_path(self, path, max_len=STATUS_BAR_MAX_PATH_LEN):
        """Truncates a long file path for display."""
        if len(path) > max_len:
            return "..." + path[-(max_len - 3):]
        return path

    def _set_busy_cursor(self):
        """Sets the application cursor to 'watch' (busy)."""
        self.config(cursor="watch") # Use the correct window config method
        self.update_idletasks()

    def _reset_cursor(self):
        """Resets the application cursor to default."""
        self.config(cursor="") # Use the correct window config method

# --- Entry Point ---
def main():
    """Application entry point."""
    try:
        import tkinter
        import tkinter.ttk
        import configparser
        import collections
    except ImportError as e:
        missing_module = str(e).split("'")[-2]
        print(f"Error: Required standard library module '{missing_module}' not found.", file=sys.stderr)
        print("Please ensure your Python installation is complete.", file=sys.stderr)
        if missing_module == 'tkinter':
             print("On Debian/Ubuntu: sudo apt-get install python3-tk", file=sys.stderr)
             print("On Fedora: sudo dnf install python3-tkinter", file=sys.stderr)
        sys.exit(1)

    app = SQLiteViewer()
    app.mainloop()

if __name__ == "__main__":
    main()