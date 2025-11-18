import os
import subprocess
from datetime import datetime
from pathlib import Path


def render_diagram(mermaid_file: str, output_dir: str = "results") -> str:
    """
    Render a Mermaid diagram file to an image and save it with a timestamp.
    
    Args:
        mermaid_file: Path to the .mmd or .mermaid file
        output_dir: Directory to save the rendered image (default: "results")
    
    Returns:
        Path to the saved image file
    
    Raises:
        FileNotFoundError: If the mermaid file doesn't exist
        subprocess.CalledProcessError: If rendering fails
    """
    # Verify the mermaid file exists
    mermaid_path = Path(mermaid_file)
    if not mermaid_path.exists():
        raise FileNotFoundError(f"Mermaid file not found: {mermaid_file}")
    
    # Create results directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_stem = mermaid_path.stem
    output_file = output_path / f"{file_stem}_{timestamp}.png"
    
    # Render the diagram using mmdc (mermaid-cli)
    # Make sure to have it installed: npm install -g @mermaid-js/mermaid-cli
    try:
        command = [
            "mmdc",
            "-i", str(mermaid_path),
            "-o", str(output_file),
            "--scale", "3",
            "--width", "1200",
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Diagram rendered successfully: {output_file}")
        return str(output_file)
    
    except FileNotFoundError:
        raise FileNotFoundError(
            "mermaid-cli (mmdc) not found. Install it with: npm install -g @mermaid-js/mermaid-cli"
        )
    except subprocess.CalledProcessError as e:
        raise subprocess.CalledProcessError(
            e.returncode,
            e.cmd,
            output=e.stdout,
            stderr=e.stderr
        )


if __name__ == "__main__":
    # Example usage
    mermaid_file = "mcp_sequence_template.mmd"
    try:
        output_file = render_diagram(mermaid_file)
        print(f"Image saved to: {output_file}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to render diagram: {e.stderr}")
