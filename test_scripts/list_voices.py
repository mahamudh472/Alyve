#!/usr/bin/env python3
import os
import sys
import argparse
import requests
from dotenv import load_dotenv

# Voice IDs that must not be deleted
active_voices = [
    'a4ciU8LDfB1trSV5WVOw', 'pRMvly99kbUzio690nse', 'v1u7dip5yJrheUxTfJY0', 
    'w3Y467lXxAeyq1E3gWI3', 'VxrlbBgFHuBd7yL1sF3B', 'joMdOLzAJZWTYaOwrtAB', 
    'tXyo60Hm6KNj7AkonSlc', '4rqhtRzTeCXHAZ71czmT', 'yiI1K3Igr4Sn8u7XaH0u', 
    'nLTsFr7Tedgc0TEr2UQv', 'fvFtaKHdqwcKuiizRE5H', 'T3IuLxv4aGpc4qn0S7gh', 
    'YDHlMHszYsg2NLury4ZM', 'a3L8RcWI6TCAm2YvDFuF', 'OuKKwsNNfJtLbA4723F8'
]

def list_voices(api_key, base_url):
    url = f"{base_url}/v1/voices"
    headers = {
        "xi-api-key": api_key,
        "accept": "application/json"
    }
    
    print(f"Fetching available voices from: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Error fetching voices: Status Code {response.status_code}")
            print(response.text[:1000])
            return
        
        data = response.json()
        voices = data.get("voices", [])
        
        if not voices:
            print("No voices found in your ElevenLabs account.")
            return
        
        print(f"\nFound {len(voices)} voice(s):\n")
        
        # Table Header
        header_format = "{:<36} | {:<25} | {:<12} | {}"
        row_separator = "-" * 110
        print(header_format.format("Voice ID", "Name", "Category", "Description"))
        print(row_separator)
        
        for voice in voices:
            voice_id = voice.get("voice_id", "N/A")
            name = voice.get("name", "N/A")
            category = voice.get("category", "N/A")
            description = voice.get("description") or ""
            
            # Truncate long names or categories to fit the table layout neatly
            name_trunc = (name[:22] + "...") if len(name) > 25 else name
            category_trunc = (category[:10] + "...") if len(category) > 12 else category
            desc_trunc = (description[:40] + "...") if len(description) > 40 else description
            
            print(header_format.format(voice_id, name_trunc, category_trunc, desc_trunc))
            
        print(row_separator)
        
    except Exception as e:
        print(f"An error occurred while calling the ElevenLabs API: {e}")

def delete_inactive_voices(api_key, base_url):
    url = f"{base_url}/v1/voices"
    headers = {
        "xi-api-key": api_key,
        "accept": "application/json"
    }
    
    print(f"Fetching voices to identify candidates for deletion...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Error fetching voices: Status Code {response.status_code}")
            print(response.text[:1000])
            return
        
        data = response.json()
        voices = data.get("voices", [])
        
        candidates = []
        for voice in voices:
            voice_id = voice.get("voice_id")
            category = voice.get("category")
            # Only delete cloned voices not in active_voices list
            if category == "cloned" and voice_id not in active_voices:
                candidates.append(voice)
                
        if not candidates:
            print("No inactive cloned voices found to delete.")
            return
            
        print(f"\nFound {len(candidates)} inactive cloned voice(s) to delete:")
        print("-" * 80)
        for voice in candidates:
            print(f"- {voice.get('voice_id')}: {voice.get('name')} (cloned)")
        print("-" * 80)
        
        confirm = input("\nAre you sure you want to delete these voices? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("Deletion cancelled.")
            return
            
        print("\nDeleting voices...")
        for voice in candidates:
            v_id = voice.get("voice_id")
            v_name = voice.get("name")
            del_url = f"{base_url}/v1/voices/{v_id}"
            try:
                del_resp = requests.delete(del_url, headers=headers, timeout=15)
                if del_resp.status_code == 200:
                    print(f"Successfully deleted {v_id} ({v_name})")
                else:
                    print(f"Failed to delete {v_id} ({v_name}): Status Code {del_resp.status_code}")
                    print(del_resp.text[:500])
            except Exception as ex:
                print(f"Error deleting {v_id} ({v_name}): {ex}")
                
    except Exception as e:
        print(f"An error occurred while calling the ElevenLabs API: {e}")

def main():
    # Load environment variables from .env file if present
    load_dotenv()
    
    # Retrieve the API key and Base URL from environment variables
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Error: ELEVENLABS_API_KEY environment variable is not set.")
        print("Please ensure it is defined in your environment or in a .env file.")
        return

    base_url = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")

    parser = argparse.ArgumentParser(description="List and manage ElevenLabs voices.")
    parser.add_argument(
        "--delete", 
        action="store_true", 
        help="Delete cloned voices that are not in the active_voices list."
    )
    args = parser.parse_args()

    if args.delete:
        delete_inactive_voices(api_key, base_url)
    else:
        list_voices(api_key, base_url)

if __name__ == "__main__":
    main()
