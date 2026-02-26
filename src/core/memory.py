# src/core/memory.py
from typing import List
from core.state import ProjectState, SceneNode

class MemoryManager:
    """
    Manages the short-term sliding window of scene summaries and long-term consolidation
    into the project's archived memory.
    """
    def __init__(self, state: ProjectState, wiki_updater, log=None):
        self.state = state
        self.wiki_updater = wiki_updater
        self.log = log

    def get_linear_path(self, target_scene_id: int) -> List[SceneNode]:
        """
        Traverse the state tree to find the linear path from root to target_scene_id.
        This handles branches.
        """
        def dfs(nodes: List[SceneNode], current_path: List[SceneNode]) -> bool:
            for node in nodes:
                current_path.append(node)
                if node.id == target_scene_id:
                    return True
                if dfs(node.branches, current_path):
                    return True
                current_path.pop()
            return False
            
        path = []
        dfs(self.state.scenes, path)
        return path

    def consolidate_memory(self, current_scene_id: int, window_size: int = 10, archive_batch_size: int = 5):
        """
        Consolidate old scene summaries into archive.
        Uses sliding window based on the depth of the current scene in the linear path.
        """
        # Get the linear path of scenes leading to this current scene
        path = self.get_linear_path(current_scene_id)
        if not path:
            if self.log:
                self.log.warning(f"Could not find linear path to scene {current_scene_id} for memory consolidation.")
            return

        current_depth = len(path)
        last_archived_depth = getattr(self.state, "last_archived_depth", 0)

        # If we have reached the threshold to archive
        if (current_depth - last_archived_depth) >= window_size:
            start_index = last_archived_depth
            end_index = last_archived_depth + archive_batch_size
            
            scenes_to_archive_nodes = path[start_index:end_index]
            
            if self.log:
                start_id = scenes_to_archive_nodes[0].id
                end_id = scenes_to_archive_nodes[-1].id
                self.log.info(f"Consolidating memory for scenes depth {start_index} to {end_index-1} (IDs {start_id} to {end_id})...")
            
            scenes_to_archive_summaries = []
            for node in scenes_to_archive_nodes:
                if node.summary:
                    scenes_to_archive_summaries.append(node.summary)
                elif node.status == "done" and self.log:
                    self.log.warning(f"Scene {node.id} is 'done' but missing summary.")

            if scenes_to_archive_summaries:
                chapter_summary = self.wiki_updater.consolidate_summaries(scenes_to_archive_summaries)
                self.state.archived_summaries.append(chapter_summary)
                # Update the new state variable for reliable sliding window
                self.state.last_archived_depth = end_index
                
                # Keep legacy field updated just in case
                self.state.last_archived_scene_id = scenes_to_archive_nodes[-1].id
                
                self.state.save()
                if self.log:
                    self.log.info(f"Memory consolidated. New archive count: {len(self.state.archived_summaries)}")
