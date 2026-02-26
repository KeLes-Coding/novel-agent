# src/plugins/version_manager.py
import os
import glob
import json
from typing import List, Dict, Any, Optional
from storage.local_store import LocalStore

class ProjectVersionManager:
    """
    Provides an API to list and retrieve all generated versions of a scene
    and global artifacts (outline, bible) for a specific run_dir.
    """
    def __init__(self, run_dir: str):
        if not os.path.exists(run_dir):
            raise ValueError(f"Run dir does not exist: {run_dir}")
        self.run_dir = run_dir
        self.store = LocalStore(run_dir)
        
    def get_scene_versions(self, scene_id: int) -> List[Dict[str, Any]]:
        """
        Scan drafting and polishing directories to aggregate all versions of a single scene.
        """
        versions = []
        
        # 1. Check AI drafted candidates (scene_xxx_v1.json, etc.)
        drafting_dir = self.store._abs("05_drafting/scenes")
        if os.path.exists(drafting_dir):
            pattern = os.path.join(drafting_dir, f"scene_{scene_id:03d}_v*.json")
            for file_path in glob.glob(pattern):
                versions.append(self._parse_file_info(file_path, "drafting_candidate"))
                
            # Check the selected/standard draft
            std_draft = os.path.join(drafting_dir, f"scene_{scene_id:03d}.json")
            if os.path.exists(std_draft):
                versions.append(self._parse_file_info(std_draft, "drafting_selected"))
                
            # Legacy drafting support (scene_xxx.md directly in draft folder)
            legacy_draft = os.path.join(drafting_dir, f"scene_{scene_id:03d}.md")
            if os.path.exists(legacy_draft):
                 versions.append(self._parse_file_info(legacy_draft, "drafting_legacy_md"))
                 
        # 2. Check polished versions
        polish_dir = self.store._abs("06_polishing/scenes")
        if os.path.exists(polish_dir):
            polish_file = os.path.join(polish_dir, f"scene_{scene_id:03d}.json")
            if os.path.exists(polish_file):
                versions.append(self._parse_file_info(polish_file, "polished"))
                
        # 3. Check Bypassed (Humanized) versions
        bypass_file = os.path.join(polish_dir, f"scene_{scene_id:03d}_bypass.md")
        if os.path.exists(bypass_file):
            versions.append(self._parse_file_info(bypass_file, "bypassed_md"))

        # Sort by modification time
        versions.sort(key=lambda x: x["mtime"])
        return versions

    def get_global_files(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get versions of ideas, outline, and bible.
        """
        results = {
            "ideation": [],
            "outline": [],
            "bible": []
        }
        
        # Ideation
        idea_dir = self.store._abs("01_ideation")
        if os.path.exists(idea_dir):
            for f in glob.glob(os.path.join(idea_dir, "*.*")):
                results["ideation"].append(self._parse_file_info(f, "global"))

        # Outline
        outline_dir = self.store._abs("02_outline")
        if os.path.exists(outline_dir):
            for f in glob.glob(os.path.join(outline_dir, "*.*")):
                results["outline"].append(self._parse_file_info(f, "global"))
                
        # Bible
        bible_dir = self.store._abs("03_bible")
        if os.path.exists(bible_dir):
            for f in glob.glob(os.path.join(bible_dir, "*.*")):
                results["bible"].append(self._parse_file_info(f, "global"))
                
        return results

    def _parse_file_info(self, file_path: str, stage: str) -> Dict[str, Any]:
        stat = os.stat(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        content_preview = ""
        word_count = 0
        
        try:
            if ext == ".json":
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    text = data.get("content", "")
                    content_preview = text[:100].replace("\n", " ") + "..."
                    word_count = len(text)
            else:
                 with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                    content_preview = text[:100].replace("\n", " ") + "..."
                    word_count = len(text)
        except Exception:
            content_preview = "<Cannot Read File>"

        return {
            "path": file_path,
            "filename": os.path.basename(file_path),
            "stage": stage,
            "mtime": stat.st_mtime,
            "size_bytes": stat.st_size,
            "word_count": word_count,
            "preview": content_preview
        }
