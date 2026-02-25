import sys
import os
import unittest
from unittest.mock import MagicMock

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from core.workflow import WorkflowEngine
from core.state import SceneNode
from agents.reader import ReaderAgent
from agents.polisher import PolisherAgent

class TestAgentPipeline(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_provider = MagicMock()
        self.mock_provider.generate.return_value.text = "Mock Generation"
        
        self.mock_store = MagicMock()
        self.mock_store._abs = lambda x: x
        
        self.mock_log = MagicMock()
        
        self.ctx = {
            "cfg": {"workflow": {"auto_polish": True}},
            "log": self.mock_log,
            "prompts": {},
            "provider": self.mock_provider,
            "store": self.mock_store,
            "run_id": "test_run",
            "interface": MagicMock(),
            "jsonl": None
        }
        
        self.engine = WorkflowEngine(self.ctx)

    def test_reader_agent(self):
        print("\nTesting Reader Agent...")
        agent = ReaderAgent(self.mock_provider)
        
        # Mock LLM response for critique
        self.mock_provider.generate.return_value.text = '''
        ```json
        {
            "score": 7.5,
            "summary": "Good start but needs work.",
            "issues": ["Pacing is slow", "Dialogue is stiff"],
            "suggestions": ["Cut the first paragraph", "Add more action"]
        }
        ```
        '''
        
        critique = agent.critique("Some draft content")
        print(f"Critique: {critique}")
        
        self.assertIn("score", critique)
        self.assertEqual(critique["score"], 7.5)
        self.assertIn("issues", critique)

    def test_polisher_agent(self):
        print("\nTesting Polisher Agent...")
        agent = PolisherAgent(self.mock_provider)
        
        critique = {
            "score": 6.0,
            "issues": ["Too verbose"],
            "suggestions": ["Shorten sentences"]
        }
        
        self.mock_provider.generate.return_value.text = "Polished Content"
        
        result = agent.polish("Original Content", critique)
        print(f"Polished: {result}")
        
        self.assertEqual(result, "Polished Content")

if __name__ == '__main__':
    unittest.main()
