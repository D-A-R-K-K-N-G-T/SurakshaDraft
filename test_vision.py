import os
from dotenv import load_dotenv
import json

load_dotenv(os.path.join("agentic_pipeline", ".env"))

from langchain_core.messages import HumanMessage, SystemMessage
from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.schemas import VisionOutput
from agentic_pipeline.images import load_image_as_data_url, build_image_block
from agentic_pipeline.prompts import VISION_SYSTEM_PROMPT

def run_vision_only():
    image_path = "broken_car.jpg"
    if not os.path.exists(image_path):
        print(f"Error: {image_path} not found.")
        return

    print(f"Sending {image_path} to Vision LLM...")
    
    content = [{
        "type": "text",
        "text": "Photos follow below, each preceded by its evidence_id and capture stage.",
    }]
    
    content.append({
        "type": "text",
        "text": "[evidence_id=IMG-001, capture_stage=scene]",
    })
    
    data_url = load_image_as_data_url(image_path)
    content.append(build_image_block(data_url))

    llm = get_structured_llm(VisionOutput, want_vision=True)
    
    print("Calling LLM...")
    try:
        result: VisionOutput = invoke_structured(llm, [
            SystemMessage(content=VISION_SYSTEM_PROMPT),
            HumanMessage(content=content),
        ])
        print("\n--- VISION OUTPUT ---")
        print(result.model_dump_json(indent=2))
    except Exception as e:
        print(f"\nAPI Call Failed: {e}")

if __name__ == "__main__":
    run_vision_only()
