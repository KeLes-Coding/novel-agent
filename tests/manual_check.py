import json
import os

def check():
    path = os.path.join("tests", "data", "fixtures.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    outline = data.get("outline")
    print(f"Outline length: {len(outline) if outline else 0}")
    
    if outline is None:
        print("FAIL: Outline is None")
        return

    if "卷" not in outline and "Volume" not in outline:
        print("FAIL: No '卷' or 'Volume' in outline")
    else:
        print("PASS: '卷' or 'Volume' found")

    keywords = ["评估", "Analysis", "Summary", "总结", "摘要"]
    found = [k for k in keywords if k in outline]
    if not found:
        print(f"FAIL: None of {keywords} found in outline")
        # Print end of outline to see what's there
        print("Tail of outline:")
        print(outline[-200:])
    else:
        print(f"PASS: Found keyword(s): {found}")

    if "#" not in outline:
        print("FAIL: No markdown header '#'")
    else:
        print("PASS: Markdown header found")

    ideation = data.get("ideation")
    if ideation is None:
        print("FAIL: Ideation is None")
    else:
        print(f"PASS: Ideation length {len(ideation)}")

if __name__ == "__main__":
    check()
