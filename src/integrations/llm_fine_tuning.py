"""
LLM Fine-Tuning System for Patch Notes Generation.

Exports training data in JSONL format for LLM fine-tuning.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger('shadowops')


class LLMFineTuning:
    """
    Manages fine-tuning data export and model integration.
    """

    def __init__(self, data_dir: Path, trainer):
        self.data_dir = data_dir
        self.trainer = trainer

        self.export_dir = self.data_dir / 'fine_tuning_exports'
        self.export_dir.mkdir(parents=True, exist_ok=True)

        logger.info("✅ LLM Fine-Tuning system initialized")

    def export_for_fine_tuning(self, project: Optional[str] = None,
                                      min_quality_score: float = 75.0,
                                      max_examples: int = 1000) -> Path:
        """
        Export training data in JSONL fine-tuning format.

        JSONL format:
        {"prompt": "...", "response": "..."}

        Args:
            project: Optional project filter
            min_quality_score: Minimum quality score to include
            max_examples: Maximum number of examples to export

        Returns:
            Path to exported file
        """
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        export_file = self.export_dir / f'finetune_{project or "all"}_{timestamp}.jsonl'

        if not self.trainer.training_data_file.exists():
            logger.warning("No training data available for export")
            return export_file

        exported_count = 0

        try:
            with open(self.trainer.training_data_file, 'r', encoding='utf-8') as f_in:
                with open(export_file, 'w', encoding='utf-8') as f_out:
                    for line in f_in:
                        if exported_count >= max_examples:
                            break

                        try:
                            example = json.loads(line)

                            # Filter by project
                            if project and example.get('project') != project:
                                continue

                            # Filter by quality score
                            if example.get('quality_score', 0) < min_quality_score:
                                continue

                            # Build prompt from CHANGELOG
                            changelog_content = example.get('changelog', '')
                            project_name = example.get('project', 'project')

                            prompt = f"""You are an expert technical writer creating patch notes for {project_name}.

# CHANGELOG INFORMATION
{changelog_content}

Create professional, detailed patch notes following this format:
- Use categories: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
- For each feature, provide comprehensive description
- Use sub-bullets for details
- Focus on user benefits

Create the patch notes now:"""

                            response = example.get('generated_notes', '')

                            # Write in JSONL format
                            entry = {
                                'prompt': prompt,
                                'response': response
                            }

                            f_out.write(json.dumps(entry) + '\n')
                            exported_count += 1

                        except Exception as e:
                            logger.debug(f"Skipped invalid training example: {e}")
                            continue

            logger.info(f"✅ Exported {exported_count} examples to {export_file}")

        except Exception as e:
            logger.error(f"Failed to export fine-tuning data: {e}", exc_info=True)

        return export_file

    def export_for_lora_fine_tuning(self, project: Optional[str] = None,
                                     min_quality_score: float = 75.0) -> Path:
        """
        Export in LoRA fine-tuning format (for advanced users).

        Format: Alpaca-style instruction dataset
        """
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        export_file = self.export_dir / f'lora_finetune_{project or "all"}_{timestamp}.json'

        if not self.trainer.training_data_file.exists():
            logger.warning("No training data available for export")
            return export_file

        dataset = []

        try:
            with open(self.trainer.training_data_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        example = json.loads(line)

                        # Filters
                        if project and example.get('project') != project:
                            continue
                        if example.get('quality_score', 0) < min_quality_score:
                            continue

                        changelog_content = example.get('changelog', '')
                        project_name = example.get('project', 'project')

                        instruction = f"Create professional patch notes for {project_name} based on the following CHANGELOG."

                        alpaca_entry = {
                            'instruction': instruction,
                            'input': changelog_content,
                            'output': example.get('generated_notes', '')
                        }

                        dataset.append(alpaca_entry)

                    except Exception as e:
                        logger.debug(f"Skipped invalid example: {e}")
                        continue

            with open(export_file, 'w', encoding='utf-8') as f:
                json.dump(dataset, f, indent=2)

            logger.info(f"✅ Exported {len(dataset)} examples in LoRA format to {export_file}")

        except Exception as e:
            logger.error(f"Failed to export LoRA format: {e}", exc_info=True)

        return export_file

    def generate_fine_tuning_script(self, export_file: Path, model_name: str = "llama3.1") -> Path:
        """
        Generate a shell script for fine-tuning (legacy, ai_learning disabled).

        Args:
            export_file: Path to exported training data
            model_name: Base model name

        Returns:
            Path to generated script
        """
        script_file = export_file.parent / f'finetune_{export_file.stem}.sh'

        script_content = f"""#!/bin/bash
# Fine-Tuning Data Export Script
# Generated: {datetime.utcnow().isoformat()}
# NOTE: ai_learning is currently disabled. This script is for reference only.

set -e

echo "🚀 Fine-tuning data exported for {model_name}..."

# Check if training data exists
if [ ! -f "{export_file}" ]; then
    echo "❌ Training data file not found: {export_file}"
    exit 1
fi

echo "📝 Training data: {export_file}"
echo "📊 Lines: $(wc -l < {export_file})"

NEW_MODEL_NAME="{model_name}-patchnotes-$(date +%Y%m%d)"

echo ""
echo "✅ Fine-tuning complete!"
echo ""
echo "To use this model in ShadowOps:"
echo "1. Update config/config.yaml:"
echo "   ai:"
echo "     primary:"
echo "       engine: codex"
echo "       models:"
echo "         standard: $NEW_MODEL_NAME"
echo ""
echo "2. Restart ShadowOps bot"
echo ""
echo "Training data: {export_file}"
echo "Model name: $NEW_MODEL_NAME"
"""

        try:
            with open(script_file, 'w', encoding='utf-8') as f:
                f.write(script_content)

            # Make executable
            script_file.chmod(0o755)

            logger.info(f"✅ Generated fine-tuning script: {script_file}")

        except Exception as e:
            logger.error(f"Failed to generate script: {e}")

        return script_file

    def export_and_prepare_fine_tuning(self, project: Optional[str] = None,
                                       min_quality_score: float = 80.0) -> Dict[str, Path]:
        """
        Complete fine-tuning preparation workflow.

        Returns:
            Dict with paths to generated files
        """
        logger.info(f"🚀 Preparing fine-tuning export for project: {project or 'all'}")

        # Export data
        jsonl_file = self.export_for_fine_tuning(project, min_quality_score)
        lora_file = self.export_for_lora_fine_tuning(project, min_quality_score)

        # Generate script
        script_file = self.generate_fine_tuning_script(jsonl_file)

        # Generate README
        readme_file = self._generate_readme(jsonl_file, lora_file, script_file)

        result = {
            'jsonl_data': jsonl_file,
            'lora_data': lora_file,
            'script': script_file,
            'readme': readme_file
        }

        logger.info(f"✅ Fine-tuning export complete. Files in: {self.export_dir}")

        return result

    def _generate_readme(self, jsonl_file: Path, lora_file: Path, script_file: Path) -> Path:
        """Generate README for fine-tuning exports."""
        readme_file = self.export_dir / 'README_FINE_TUNING.md'

        readme_content = f"""# Fine-Tuning Guide for Patch Notes Generation

Generated: {datetime.utcnow().isoformat()}

## 📁 Exported Files

- **JSONL Format**: `{jsonl_file.name}` - Ready for fine-tuning
- **LoRA Format**: `{lora_file.name}` - For advanced LoRA fine-tuning
- **Fine-Tuning Script**: `{script_file.name}` - Automated fine-tuning script

## 🚀 Quick Start

1. **Run the automated script**:
   ```bash
   cd {self.export_dir}
   ./{script_file.name}
   ```

2. **Update ShadowOps config**:
   ```yaml
   # config/config.yaml
   ai:
     primary:
       engine: codex
       models:
         standard: gpt-5.3-codex
   ```

3. **Restart ShadowOps**:
   ```bash
   systemctl --user restart shadowops-bot.service
   ```

## 📖 Manual Fine-Tuning (Advanced)

### Option 1: JSONL Direct

The exported JSONL file can be used with any fine-tuning platform
that supports prompt/response pairs.

### Option 2: LoRA Fine-Tuning

Use the `{lora_file.name}` file with tools like:
- `llama.cpp` with LoRA support
- Hugging Face `transformers` library
- `axolotl` fine-tuning framework

Example with axolotl:
```bash
# Install axolotl
pip install axolotl

# Create config (axolotl format)
# Use {lora_file.name} as dataset

# Run fine-tuning
accelerate launch -m axolotl.cli.train your_config.yml
```

## 📊 Training Data Statistics

Check the logs for:
- Number of examples exported
- Quality score threshold used
- Date range of training data

## 🔧 Troubleshooting

### Issue: Model performs worse after fine-tuning

**Solution:**
- Increase `min_quality_score` threshold (try 85+)
- Ensure you have ≥100 high-quality examples
- Adjust learning rate (for LoRA)

### Issue: Model generates too short/long responses

**Solution:**
- Adjust `PARAMETER temperature` in Modelfile
- Fine-tune with more examples at desired length

### Issue: Model ignores CHANGELOG content

**Solution:**
- Ensure training examples have complete CHANGELOG sections
- Increase training examples
- Use higher learning rate

## 📈 Monitoring Performance

After fine-tuning:
1. Test with sample CHANGELOGs
2. Compare quality scores vs base model
3. Collect user feedback
4. Iterate if needed

## 🔄 Updating the Fine-Tuned Model

As you collect more high-quality examples:
1. Export new training data
2. Re-run fine-tuning script
3. Compare new model vs old
4. Deploy if improved

---

**Note:** Fine-tuning requires significant computational resources. For production use, consider using a dedicated machine or cloud GPU instance.
"""

        try:
            with open(readme_file, 'w', encoding='utf-8') as f:
                f.write(readme_content)

            logger.info(f"✅ Generated README: {readme_file}")
        except Exception as e:
            logger.error(f"Failed to generate README: {e}")

        return readme_file


def get_llm_fine_tuning(data_dir: Path, trainer) -> LLMFineTuning:
    """Get LLMFineTuning instance."""
    return LLMFineTuning(data_dir, trainer)
