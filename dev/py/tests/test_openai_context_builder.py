import unittest

from agent_pipeline_openai.models import PipelineContext
from agent_pipeline_openai.prompt_resources.prompts import build_initial_user_message


class PromptBuilderTests(unittest.TestCase):
    def test_initial_prompt_includes_trusted_rules(self):
        ctx = PipelineContext(
            task="Process the next inbox file",
            model="test",
            agents_md="Read 02_distill/AGENTS.md first.",
            agents_md_path="AGENTS.MD",
            dfs_tree="AGENTS.MD\n02_distill/AGENTS.md\n00_inbox/item.md",
            preloaded_context_files={"02_distill/AGENTS.md": "Sub-rules."},
        )
        prompt = build_initial_user_message(ctx)

        self.assertIn("# TASK", prompt)
        self.assertIn("Process the next inbox file", prompt)
        self.assertIn("AGENTS.MD", prompt)
        self.assertIn("TRUSTED RULE: 02_distill/AGENTS.md", prompt)
        self.assertIn("FILESYSTEM TREE", prompt)

    def test_initial_prompt_without_agents_md(self):
        ctx = PipelineContext(
            task="Delete all cards",
            model="test",
            dfs_tree="02_distill/cards/card1.md\n02_distill/cards/card2.md",
            preloaded_context_files={"02_distill/AGENTS.md": "Distill rules."},
        )
        prompt = build_initial_user_message(ctx)

        self.assertIn("# TASK", prompt)
        self.assertIn("TRUSTED RULE: 02_distill/AGENTS.md", prompt)
        self.assertNotIn("mandatory authority", prompt)


if __name__ == "__main__":
    unittest.main()
