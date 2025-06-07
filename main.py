# This is free and unencumbered software released into the public domain.
# 
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
# 
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
# 
# For more information, please refer to <https://unlicense.org>

import dearpygui.dearpygui as dpg
from pathlib import Path
from typing import Optional, List, Dict, Tuple, NamedTuple
from dataclasses import dataclass
import subprocess
import re
import os
import difflib


@dataclass
class ConflictMarkers:
    """Git conflict markers in a file"""
    start: int
    middle: int
    end: int
    base_content: List[str]
    local_content: List[str]
    remote_content: List[str]
    conflict_id: int
    is_resolved: bool = False
    resolved_with: Optional[str] = None  # 'local', 'remote', 'base', or 'manual'
    original_start: int = 0
    original_end: int = 0
    resolved_lines: List[str] = None  # What was actually chosen
    rejected_lines: List[str] = None  # What was rejected (for highlighting)


@dataclass
class DiffHighlight:
    """Represents a highlighted diff region"""
    start_line: int
    end_line: int
    highlight_type: str  # 'added', 'removed', 'changed'
    content: List[str]


@dataclass
class GitFileStatus:
    """Status of a file in git"""
    path: Path
    status: str
    has_conflicts: bool = False


class GitRepository:
    """Git repository operations"""
    
    def __init__(self, repo_path: Optional[Path] = None):
        self.repo_path = repo_path or Path.cwd()
        self._validate_repo()
    
    def _validate_repo(self) -> bool:
        """Check if current directory is a git repository"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False
    
    def get_conflicted_files(self) -> List[GitFileStatus]:
        """Get list of files with merge conflicts"""
        try:
            result = subprocess.run(
                ['git', 'diff', '--name-only', '--diff-filter=U'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            files = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    files.append(GitFileStatus(
                        path=Path(line),
                        status='unmerged',
                        has_conflicts=True
                    ))
            return files
            
        except subprocess.CalledProcessError:
            return []
    
    def get_file_versions(self, file_path: Path) -> Dict[str, str]:
        """Get different versions of a file (base, local, remote)"""
        versions = {}
        
        try:
            # Get base version (common ancestor) - stage 1
            result = subprocess.run(
                ['git', 'show', f':1:{file_path}'],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                versions['base'] = result.stdout
            
            # Get local version (our side/HEAD) - stage 2  
            result = subprocess.run(
                ['git', 'show', f':2:{file_path}'],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                versions['local'] = result.stdout
            
            # Get remote version (their side) - stage 3
            result = subprocess.run(
                ['git', 'show', f':3:{file_path}'],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                versions['remote'] = result.stdout
                
        except subprocess.CalledProcessError:
            pass
        
        return versions
    
    def parse_conflict_markers(self, content: str) -> List[ConflictMarkers]:
        """Parse Git conflict markers from file content"""
        lines = content.splitlines()
        conflicts = []
        i = 0
        conflict_id = 0
        
        while i < len(lines):
            line = lines[i]
            
            if line.startswith('<<<<<<<'):
                start_idx = i
                local_content = []
                base_content = []
                remote_content = []
                
                i += 1
                while i < len(lines) and not lines[i].startswith('|||||||') and not lines[i].startswith('======='):
                    local_content.append(lines[i])
                    i += 1
                
                if i < len(lines) and lines[i].startswith('|||||||'):
                    i += 1
                    while i < len(lines) and not lines[i].startswith('======='):
                        base_content.append(lines[i])
                        i += 1
                
                if i < len(lines) and lines[i].startswith('======='):
                    middle_idx = i
                    i += 1
                    
                    while i < len(lines) and not lines[i].startswith('>>>>>>>'):
                        remote_content.append(lines[i])
                        i += 1
                    
                    if i < len(lines) and lines[i].startswith('>>>>>>>'):
                        end_idx = i
                        conflicts.append(ConflictMarkers(
                            start=start_idx,
                            middle=middle_idx,
                            end=end_idx,
                            base_content=base_content,
                            local_content=local_content,
                            remote_content=remote_content,
                            conflict_id=conflict_id
                        ))
                        conflict_id += 1
            i += 1
        
        return conflicts
    
    def resolve_conflict(self, file_path: Path, resolved_content: str) -> bool:
        """Mark conflict as resolved by writing content and staging"""
        try:
            (self.repo_path / file_path).write_text(resolved_content, encoding='utf-8')
            subprocess.run(
                ['git', 'add', str(file_path)],
                cwd=self.repo_path,
                check=True
            )
            return True
            
        except (subprocess.CalledProcessError, IOError):
            return False


class DiffHighlighter:
    """Handles diff highlighting and visualization"""
    
    @staticmethod
    def generate_line_diff(chosen_lines: List[str], rejected_lines: List[str]) -> List[DiffHighlight]:
        """Generate line-by-line diff highlights between chosen and rejected content"""
        highlights = []
        
        if not chosen_lines and not rejected_lines:
            return highlights
        
        # Use difflib to get detailed diff
        differ = difflib.unified_diff(
            rejected_lines, chosen_lines,
            lineterm='', n=0
        )
        
        diff_lines = list(differ)
        current_line = 0
        
        for line in diff_lines:
            if line.startswith('@@'):
                # Parse hunk header to get line numbers
                match = re.search(r'-(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?', line)
                if match:
                    current_line = int(match.group(3)) - 1
            elif line.startswith('-'):
                # Line was removed (was in rejected, not in chosen)
                highlights.append(DiffHighlight(
                    start_line=current_line,
                    end_line=current_line + 1,
                    highlight_type='removed',
                    content=[line[1:]]
                ))
            elif line.startswith('+'):
                # Line was added (is in chosen, wasn't in rejected)
                highlights.append(DiffHighlight(
                    start_line=current_line,
                    end_line=current_line + 1,
                    highlight_type='added',
                    content=[line[1:]]
                ))
                current_line += 1
            elif not line.startswith('@@') and not line.startswith('\\'):
                current_line += 1
        
        return highlights
    
    @staticmethod
    def apply_highlights_to_text(text: str, highlights: List[DiffHighlight]) -> str:
        """Apply visual highlights to text content"""
        if not highlights:
            return text
        
        lines = text.splitlines()
        highlighted_lines = []
        
        for i, line in enumerate(lines):
            line_highlighted = False
            
            for highlight in highlights:
                if highlight.start_line <= i < highlight.end_line:
                    if highlight.highlight_type == 'added':
                        highlighted_lines.append(f"[+] {line}")
                    elif highlight.highlight_type == 'removed':
                        highlighted_lines.append(f"[-] {line}")
                    else:
                        highlighted_lines.append(f"[~] {line}")
                    line_highlighted = True
                    break
            
            if not line_highlighted:
                highlighted_lines.append(f"    {line}")
        
        return '\n'.join(highlighted_lines)
    
    @staticmethod
    def create_rejection_preview(chosen_content: List[str], rejected_content: List[str]) -> str:
        """Create a preview showing what was rejected with diff highlighting"""
        if not rejected_content:
            return "// No alternative content to show"
        
        preview_lines = ["// ===== REJECTED ALTERNATIVE ====="]
        
        if chosen_content == rejected_content:
            preview_lines.append("// (Identical to chosen content)")
            preview_lines.extend(f"// {line}" for line in rejected_content)
        else:
            # Show the rejected content with diff markers
            differ = difflib.unified_diff(
                chosen_content, rejected_content,
                lineterm='', n=1
            )
            
            in_diff = False
            for line in differ:
                if line.startswith('@@'):
                    in_diff = True
                    preview_lines.append(f"// {line}")
                elif line.startswith('-'):
                    preview_lines.append(f"// CHOSEN:   {line[1:]}")
                elif line.startswith('+'):
                    preview_lines.append(f"// REJECTED: {line[1:]}")
                elif line.startswith(' ') and in_diff:
                    preview_lines.append(f"//          {line[1:]}")
        
        preview_lines.append("// ===== END REJECTED =====")
        return '\n'.join(preview_lines)


class GitMergeApp:
    """Main application class with Git integration"""
    
    def __init__(self):
        self.git_repo: Optional[GitRepository] = None
        self.current_file: Optional[Path] = None
        self.conflicted_files: List[GitFileStatus] = []
        self.current_conflicts: List[ConflictMarkers] = []
        self.original_conflicts: List[ConflictMarkers] = []
        self.file_versions: Dict[str, str] = {}
        self.selected_conflict_index: int = -1
        self.original_content: str = ""
        self.diff_highlighter = DiffHighlighter()
        self.show_rejection_preview: bool = True
        
        self.setup_dpg()
        self.create_ui()
        self.initialize_git()
    
    def setup_dpg(self) -> None:
        """Initialize DearPyGui"""
        dpg.create_context()
        dpg.create_viewport(
            title="Git Merge Tool - Enhanced with Meld-style Highlighting",
            width=1800,
            height=1000,
            min_width=1600,
            min_height=800
        )
        dpg.setup_dearpygui()
    
    def create_ui(self) -> None:
        """Create the main user interface"""
        with dpg.window(label="Git Merge Tool", tag="main_window"):
            # Menu bar
            with dpg.menu_bar():
                with dpg.menu(label="Git"):
                    dpg.add_menu_item(label="Scan for Conflicts", callback=self.scan_conflicts)
                    dpg.add_menu_item(label="Change Repository", callback=self.change_repo_dialog)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Refresh", callback=self.refresh_current_file)
                
                with dpg.menu(label="Resolve"):
                    dpg.add_menu_item(label="Accept All Local", callback=lambda: self.resolve_with_version('local'))
                    dpg.add_menu_item(label="Accept All Remote", callback=lambda: self.resolve_with_version('remote'))
                    dpg.add_menu_item(label="Accept All Base", callback=lambda: self.resolve_with_version('base'))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Mark as Resolved", callback=self.mark_resolved)
                
                with dpg.menu(label="View"):
                    dpg.add_checkbox(label="Show Rejection Preview", default_value=True, 
                                   callback=self.toggle_rejection_preview)
                    dpg.add_menu_item(label="Clear All Highlights", callback=self.clear_all_highlights)
            
            # Repository info
            with dpg.group(horizontal=True):
                dpg.add_text("Repository: ", color=[200, 200, 200])
                dpg.add_text("Not initialized", tag="repo_path", color=[255, 200, 200])
                dpg.add_spacer(width=50)
                dpg.add_button(label="Scan Conflicts", callback=self.scan_conflicts)
                dpg.add_button(label="Refresh", callback=self.refresh_current_file)
            
            dpg.add_separator()
            
            # Main layout
            with dpg.group(horizontal=True):
                # Left sidebar - conflict file list and individual conflicts
                with dpg.child_window(label="Navigation", width=350, height=750, border=True):
                    dpg.add_text("Conflicted Files", color=[255, 150, 150])
                    dpg.add_separator()
                    dpg.add_listbox(
                        items=[],
                        width=330,
                        num_items=8,
                        tag="conflict_list",
                        callback=self.on_file_selected
                    )
                    
                    dpg.add_separator()
                    dpg.add_text("Current File:", color=[200, 200, 200])
                    dpg.add_text("None selected", tag="current_file_label")
                    
                    dpg.add_separator()
                    dpg.add_text("Individual Conflicts", color=[150, 255, 150])
                    dpg.add_separator()
                    
                    # Conflict navigation
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="<<", callback=self.prev_conflict, width=40)
                        dpg.add_button(label=">>", callback=self.next_conflict, width=40)
                        dpg.add_text("0/0", tag="conflict_nav")
                    
                    # Individual conflict list
                    dpg.add_listbox(
                        items=[],
                        width=330,
                        num_items=10,
                        tag="individual_conflicts",
                        callback=self.on_conflict_selected
                    )
                    
                    dpg.add_separator()
                    
                    # Individual conflict resolution buttons
                    dpg.add_text("Resolve Selected Conflict:", color=[200, 200, 200])
                    dpg.add_button(label="Accept Local", callback=self.accept_local_conflict, width=110)
                    dpg.add_button(label="Accept Remote", callback=self.accept_remote_conflict, width=110)
                    dpg.add_button(label="Accept Base", callback=self.accept_base_conflict, width=110)
                    
                    dpg.add_separator()
                    dpg.add_text("Change Resolution:", color=[255, 200, 100])
                    dpg.add_button(label="Revert to Original", callback=self.revert_conflict, width=110)
                    dpg.add_button(label="Restore All", callback=self.restore_all_conflicts, width=110)
                
                # Right side - three-pane diff view with highlighting
                with dpg.group():
                    # File info and controls
                    with dpg.group(horizontal=True):
                        dpg.add_text("Total conflicts:", color=[200, 200, 200])
                        dpg.add_text("0", tag="conflict_count")
                        dpg.add_spacer(width=30)
                        dpg.add_text("Resolved:", color=[150, 255, 150])
                        dpg.add_text("0", tag="resolved_count")
                        dpg.add_spacer(width=50)
                        dpg.add_button(label="Accept All Local", callback=lambda: self.resolve_with_version('local'))
                        dpg.add_button(label="Accept All Remote", callback=lambda: self.resolve_with_version('remote'))
                        dpg.add_button(label="Mark All Resolved", callback=self.mark_resolved)
                    
                    dpg.add_separator()
                    
                    # Four-pane view: Base, Working Copy, Remote, Rejection Preview
                    with dpg.group(horizontal=True):
                        # Base version (common ancestor)
                        with dpg.child_window(label="Base (Common Ancestor)", width=350, height=650, border=True):
                            dpg.add_text("BASE (Common Ancestor)", color=[200, 200, 200])
                            dpg.add_separator()
                            dpg.add_input_text(
                                multiline=True,
                                readonly=True,
                                width=330,
                                height=600,
                                tag="base_text"
                            )
                        
                        # Working copy (with conflict markers and highlighting)
                        with dpg.child_window(label="Working Copy (Edit Here)", width=350, height=650, border=True):
                            dpg.add_text("WORKING COPY (Edit to resolve)", color=[255, 255, 150])
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Remove Markers", callback=self.remove_conflict_markers, width=100)
                                dpg.add_button(label="Show Diff", callback=self.show_diff_highlights, width=80)
                                dpg.add_button(label="Clear", callback=self.clear_working_highlights, width=60)
                            dpg.add_separator()
                            dpg.add_input_text(
                                multiline=True,
                                width=330,
                                height=570,
                                tag="local_text",
                                callback=self.on_text_edited
                            )
                        
                        # Remote version (incoming changes)
                        with dpg.child_window(label="Remote (Incoming)", width=350, height=650, border=True):
                            dpg.add_text("REMOTE (Incoming Changes)", color=[255, 150, 150])
                            dpg.add_separator()
                            dpg.add_input_text(
                                multiline=True,
                                readonly=True,
                                width=330,
                                height=600,
                                tag="remote_text"
                            )
                        
                        # Rejection preview pane (Meld-style)
                        with dpg.child_window(label="Rejected Alternative", width=350, height=650, border=True):
                            dpg.add_text("REJECTED ALTERNATIVE", color=[255, 200, 100])
                            dpg.add_text("(What you didn't choose)", color=[180, 180, 180])
                            dpg.add_separator()
                            dpg.add_input_text(
                                multiline=True,
                                readonly=True,
                                width=330,
                                height=580,
                                tag="rejection_preview",
                                default_value="// Make a choice to see rejected alternative"
                            )
            
            # Status bar
            dpg.add_separator()
            dpg.add_text("Ready - Initialize Git repository to begin", tag="status_text", color=[150, 150, 150])
        
        dpg.set_primary_window("main_window", True)
    
    def initialize_git(self) -> None:
        """Initialize Git repository"""
        try:
            self.git_repo = GitRepository()
            dpg.set_value("repo_path", str(self.git_repo.repo_path))
            dpg.configure_item("repo_path", color=[150, 255, 150])
            self.update_status("Git repository initialized")
            self.scan_conflicts()
        except Exception as e:
            self.update_status(f"No Git repository found: {str(e)}")
    
    def update_status(self, message: str) -> None:
        """Update status bar message"""
        dpg.set_value("status_text", message)
    
    def scan_conflicts(self) -> None:
        """Scan repository for merge conflicts"""
        if not self.git_repo:
            self.update_status("No Git repository initialized")
            return
        
        try:
            self.conflicted_files = self.git_repo.get_conflicted_files()
            
            file_names = [str(f.path) for f in self.conflicted_files]
            dpg.configure_item("conflict_list", items=file_names)
            
            if self.conflicted_files:
                self.update_status(f"Found {len(self.conflicted_files)} conflicted files")
            else:
                self.update_status("No merge conflicts found")
                
        except Exception as e:
            self.update_status(f"Error scanning conflicts: {str(e)}")
    
    def on_file_selected(self, sender, app_data) -> None:
        """Handle file selection from conflict list"""
        if not app_data or not self.conflicted_files:
            return
        
        selected_path = Path(app_data)
        selected_file = None
        
        for file_status in self.conflicted_files:
            if file_status.path == selected_path:
                selected_file = file_status
                break
        
        if selected_file:
            self.load_conflicted_file(selected_file.path)
        else:
            self.update_status(f"Could not find file: {app_data}")
    
    def load_conflicted_file(self, file_path: Path) -> None:
        """Load a conflicted file and show all versions"""
        if not self.git_repo:
            return
        
        try:
            self.current_file = file_path
            dpg.set_value("current_file_label", str(file_path))
            
            self.file_versions = self.git_repo.get_file_versions(file_path)
            
            working_copy = (self.git_repo.repo_path / file_path).read_text(encoding='utf-8')
            self.original_content = working_copy
            self.current_conflicts = self.git_repo.parse_conflict_markers(working_copy)
            
            # Create backup of original conflicts
            self.original_conflicts = []
            for conflict in self.current_conflicts:
                original_conflict = ConflictMarkers(
                    start=conflict.start,
                    middle=conflict.middle,
                    end=conflict.end,
                    base_content=conflict.base_content.copy(),
                    local_content=conflict.local_content.copy(),
                    remote_content=conflict.remote_content.copy(),
                    conflict_id=conflict.conflict_id,
                    original_start=conflict.start,
                    original_end=conflict.end
                )
                self.original_conflicts.append(original_conflict)
            
            # Update UI
            dpg.set_value("base_text", self.file_versions.get('base', 'Not available'))
            dpg.set_value("local_text", working_copy)
            dpg.set_value("remote_text", self.file_versions.get('remote', 'Not available'))
            dpg.set_value("rejection_preview", "// Make a choice to see rejected alternative")
            
            self.update_conflict_display()
            self.selected_conflict_index = 0 if self.current_conflicts else -1
            
            self.update_status(f"Loaded {file_path} with {len(self.current_conflicts)} conflicts")
            
        except Exception as e:
            self.update_status(f"Error loading file: {str(e)}")
    
    def update_conflict_display(self) -> None:
        """Update the conflict display and counts"""
        total_conflicts = len(self.current_conflicts)
        resolved_conflicts = sum(1 for c in self.current_conflicts if c.is_resolved)
        
        dpg.set_value("conflict_count", str(total_conflicts))
        dpg.set_value("resolved_count", str(resolved_conflicts))
        
        # Update individual conflicts list with resolution status
        conflict_items = []
        for i, conflict in enumerate(self.current_conflicts):
            if conflict.is_resolved:
                status = f"✓({conflict.resolved_with or 'manual'})"
            else:
                status = "✗"
            
            local_preview = conflict.local_content[0][:25] + "..." if conflict.local_content else "Empty"
            remote_preview = conflict.remote_content[0][:25] + "..." if conflict.remote_content else "Empty"
            conflict_items.append(f"{status} Conflict {i+1}: {local_preview} vs {remote_preview}")
        
        dpg.configure_item("individual_conflicts", items=conflict_items)
        
        # Update navigation
        if self.selected_conflict_index >= 0 and total_conflicts > 0:
            dpg.set_value("conflict_nav", f"{self.selected_conflict_index + 1}/{total_conflicts}")
        else:
            dpg.set_value("conflict_nav", "0/0")
    
    def on_conflict_selected(self, sender, app_data) -> None:
        """Handle individual conflict selection"""
        if not app_data or not self.current_conflicts:
            return
        
        try:
            conflict_text = app_data
            if "Conflict " in conflict_text:
                conflict_num = int(conflict_text.split("Conflict ")[1].split(":")[0]) - 1
                self.selected_conflict_index = conflict_num
                self.highlight_selected_conflict()
                self.update_conflict_display()
        except (ValueError, IndexError):
            pass
    
    def prev_conflict(self) -> None:
        """Navigate to previous conflict"""
        if self.current_conflicts and self.selected_conflict_index > 0:
            self.selected_conflict_index -= 1
            self.highlight_selected_conflict()
            self.update_conflict_display()
    
    def next_conflict(self) -> None:
        """Navigate to next conflict"""
        if self.current_conflicts and self.selected_conflict_index < len(self.current_conflicts) - 1:
            self.selected_conflict_index += 1
            self.highlight_selected_conflict()
            self.update_conflict_display()
    
    def highlight_selected_conflict(self) -> None:
        """Highlight the currently selected conflict in the working copy"""
        if not self.current_conflicts or self.selected_conflict_index < 0:
            return
        
        conflict = self.current_conflicts[self.selected_conflict_index]
        self.update_status(f"Selected conflict {self.selected_conflict_index + 1} at lines {conflict.start}-{conflict.end}")
    
    def show_diff_highlights(self) -> None:
        """Show diff highlighting in the working copy"""
        content = dpg.get_value("local_text")
        if not content:
            return
        
        # This adds simple visual markers - in a more advanced implementation,
        # you'd use proper text highlighting with colors
        lines = content.splitlines()
        highlighted_lines = []
        
        for i, line in enumerate(lines):
            if line.startswith('<<<<<<<'):
                highlighted_lines.append(f"🔴 CONFLICT START: {line}")
            elif line.startswith('======='):
                highlighted_lines.append(f"🟡 CONFLICT MIDDLE: {line}")
            elif line.startswith('>>>>>>>'):
                highlighted_lines.append(f"🔴 CONFLICT END: {line}")
            elif line.startswith('|||||||'):
                highlighted_lines.append(f"🔵 BASE MARKER: {line}")
            else:
                # Check if this line is part of a conflict
                in_conflict = False
                for conflict in self.current_conflicts:
                    if conflict.start <= i <= conflict.end and not conflict.is_resolved:
                        if conflict.start < i < conflict.middle:
                            highlighted_lines.append(f"🟢 LOCAL: {line}")
                        elif conflict.middle < i < conflict.end:
                            highlighted_lines.append(f"🔴 REMOTE: {line}")
                        else:
                            highlighted_lines.append(line)
                        in_conflict = True
                        break
                
                if not in_conflict:
                    highlighted_lines.append(line)
        
        dpg.set_value("local_text", '\n'.join(highlighted_lines))
        self.update_status("Added diff highlighting")
    
    def clear_working_highlights(self) -> None:
        """Clear highlighting from working copy"""
        content = dpg.get_value("local_text")
        if not content:
            return
        
        lines = content.splitlines()
        cleaned_lines = []
        
        for line in lines:
            # Remove our highlighting markers
            if line.startswith('🔴 ') or line.startswith('🟡 ') or line.startswith('🔵 ') or line.startswith('🟢 '):
                if 'CONFLICT' in line or 'BASE MARKER' in line:
                    # Keep the actual git markers
                    cleaned_lines.append(line.split(': ', 1)[1] if ': ' in line else line)
                elif 'LOCAL: ' in line:
                    cleaned_lines.append(line.replace('🟢 LOCAL: ', ''))
                elif 'REMOTE: ' in line:
                    cleaned_lines.append(line.replace('🔴 REMOTE: ', ''))
                else:
                    cleaned_lines.append(line)
            else:
                cleaned_lines.append(line)
        
        dpg.set_value("local_text", '\n'.join(cleaned_lines))
        self.update_status("Cleared diff highlighting")
   
    def clear_all_highlights(self) -> None:
        """Clear all highlights and restore original content"""
        if self.current_file and self.git_repo:
            try:
                original_content = (self.git_repo.repo_path / self.current_file).read_text(encoding='utf-8')
                dpg.set_value("local_text", original_content)
                self.update_status("Cleared all highlights")
            except Exception as e:
                self.update_status(f"Error clearing highlights: {str(e)}")
    
    def remove_conflict_markers(self) -> None:
        """Remove conflict markers from working copy"""
        content = dpg.get_value("local_text")
        if not content:
            return
        
        lines = content.splitlines()
        cleaned_lines = []
        
        for line in lines:
            if not (line.startswith('<<<<<<<') or line.startswith('=======') or 
                   line.startswith('>>>>>>>') or line.startswith('|||||||')):
                cleaned_lines.append(line)
        
        dpg.set_value("local_text", '\n'.join(cleaned_lines))
        self.update_status("Removed conflict markers")
    
    def accept_local_conflict(self) -> None:
        """Accept local version for selected conflict"""
        if self.selected_conflict_index >= 0 and self.selected_conflict_index < len(self.current_conflicts):
            conflict = self.current_conflicts[self.selected_conflict_index]
            self._resolve_single_conflict(conflict, 'local')
    
    def accept_remote_conflict(self) -> None:
        """Accept remote version for selected conflict"""
        if self.selected_conflict_index >= 0 and self.selected_conflict_index < len(self.current_conflicts):
            conflict = self.current_conflicts[self.selected_conflict_index]
            self._resolve_single_conflict(conflict, 'remote')
    
    def accept_base_conflict(self) -> None:
        """Accept base version for selected conflict"""
        if self.selected_conflict_index >= 0 and self.selected_conflict_index < len(self.current_conflicts):
            conflict = self.current_conflicts[self.selected_conflict_index]
            self._resolve_single_conflict(conflict, 'base')
    
    def _resolve_single_conflict(self, conflict: ConflictMarkers, resolution: str) -> None:
        """Resolve a single conflict with Meld-style highlighting"""
        if resolution == 'local':
            chosen_content = conflict.local_content
            rejected_content = conflict.remote_content
        elif resolution == 'remote':
            chosen_content = conflict.remote_content
            rejected_content = conflict.local_content
        else:  # base
            chosen_content = conflict.base_content
            rejected_content = conflict.local_content + conflict.remote_content
        
        # Mark conflict as resolved
        conflict.is_resolved = True
        conflict.resolved_with = resolution
        conflict.resolved_lines = chosen_content.copy()
        conflict.rejected_lines = rejected_content.copy()
        
        # Update working copy with resolved content
        self._update_working_copy_with_resolution(conflict, chosen_content)
        
        # Show rejection preview (Meld-style)
        self._show_rejection_preview(chosen_content, rejected_content, resolution)
        
        self.update_conflict_display()
        self.update_status(f"Resolved conflict {self.selected_conflict_index + 1} with {resolution}")
    
    def _update_working_copy_with_resolution(self, conflict: ConflictMarkers, chosen_content: List[str]) -> None:
        """Update working copy by replacing conflict with chosen content"""
        content = dpg.get_value("local_text")
        lines = content.splitlines()
        
        # Replace conflict section with chosen content
        new_lines = (
            lines[:conflict.start] + 
            chosen_content + 
            lines[conflict.end + 1:]
        )
        
        # Update line numbers for remaining conflicts
        lines_removed = (conflict.end - conflict.start + 1) - len(chosen_content)
        for other_conflict in self.current_conflicts:
            if other_conflict.conflict_id != conflict.conflict_id and other_conflict.start > conflict.end:
                other_conflict.start -= lines_removed
                other_conflict.middle -= lines_removed
                other_conflict.end -= lines_removed
        
        dpg.set_value("local_text", '\n'.join(new_lines))
    
    def _show_rejection_preview(self, chosen_content: List[str], rejected_content: List[str], resolution: str) -> None:
        """Show the rejected alternative in Meld style"""
        if not self.show_rejection_preview:
            return
        
        preview_text = self.diff_highlighter.create_rejection_preview(chosen_content, rejected_content)
        
        # Add context about the resolution
        header = [
            f"// RESOLUTION: Chose {resolution.upper()}",
            f"// Conflict {self.selected_conflict_index + 1} resolved",
            "// " + "="*40,
            ""
        ]
        
        full_preview = '\n'.join(header) + '\n' + preview_text
        dpg.set_value("rejection_preview", full_preview)
    
    def revert_conflict(self) -> None:
        """Revert selected conflict to original state"""
        if self.selected_conflict_index >= 0 and self.selected_conflict_index < len(self.current_conflicts):
            conflict = self.current_conflicts[self.selected_conflict_index]
            original = self.original_conflicts[self.selected_conflict_index]
            
            # Restore original conflict
            conflict.is_resolved = False
            conflict.resolved_with = None
            conflict.resolved_lines = None
            conflict.rejected_lines = None
            
            # Reload the file to restore original conflict markers
            if self.current_file:
                self.load_conflicted_file(self.current_file)
            
            self.update_status(f"Reverted conflict {self.selected_conflict_index + 1}")
    
    def restore_all_conflicts(self) -> None:
        """Restore all conflicts to original state"""
        if self.current_file:
            self.load_conflicted_file(self.current_file)
            dpg.set_value("rejection_preview", "// All conflicts restored to original state")
            self.update_status("Restored all conflicts to original state")
    
    def resolve_with_version(self, version: str) -> None:
        """Resolve all conflicts with specified version"""
        if not self.current_conflicts:
            return
        
        for conflict in self.current_conflicts:
            if not conflict.is_resolved:
                self._resolve_single_conflict(conflict, version)
        
        self.update_status(f"Resolved all conflicts with {version}")
    
    def mark_resolved(self) -> None:
        """Mark current file as resolved in Git"""
        if not self.current_file or not self.git_repo:
            return
        
        content = dpg.get_value("local_text")
        if self.git_repo.resolve_conflict(self.current_file, content):
            self.update_status(f"Marked {self.current_file} as resolved in Git")
            # Remove from conflicted files list
            self.conflicted_files = [f for f in self.conflicted_files if f.path != self.current_file]
            file_names = [str(f.path) for f in self.conflicted_files]
            dpg.configure_item("conflict_list", items=file_names)
        else:
            self.update_status("Failed to mark file as resolved")
    
    def on_text_edited(self) -> None:
        """Handle manual text editing in working copy"""
        # Check if conflicts have been manually resolved
        content = dpg.get_value("local_text")
        remaining_conflicts = self.git_repo.parse_conflict_markers(content) if self.git_repo else []
        
        if len(remaining_conflicts) != len(self.current_conflicts):
            self.current_conflicts = remaining_conflicts
            self.update_conflict_display()
            self.update_status("Content manually edited - conflict list updated")
    
    def toggle_rejection_preview(self, sender, app_data) -> None:
        """Toggle rejection preview visibility"""
        self.show_rejection_preview = app_data
        if not app_data:
            dpg.set_value("rejection_preview", "// Rejection preview disabled")
    
    def refresh_current_file(self) -> None:
        """Refresh current file from disk"""
        if self.current_file:
            self.load_conflicted_file(self.current_file)
    
    def change_repo_dialog(self) -> None:
        """Show dialog to change repository"""
        # This would open a file dialog - simplified for now
        self.update_status("Repository change not implemented - restart in different directory")
    
    def run(self) -> None:
        """Run the application"""
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()


if __name__ == "__main__":
    app = GitMergeApp()
    app.run()
