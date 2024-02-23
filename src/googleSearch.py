import requests
import os
from bs4 import BeautifulSoup
import tiktoken
from dotenv import load_dotenv
import time
from datetime import datetime
from openai import OpenAI

client = OpenAI()
load_dotenv()

encoding = tiktoken.encoding_for_model("gpt-4")

def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model("gpt-4")
    num_tokens = len(encoding.encode(string))
    return num_tokens

def getGPTAnswer(systemPrompts:list, question:str):
        message_objects = []
        system_prompt = {"role": 'system', "content": f'You must try to answer this question "{question}" with content from the following texts.'}
        message_objects.append(system_prompt)

        for prompt in systemPrompts:
            message_objects.append({"role": 'system', "content": f'{prompt}'})
                
        response = client.chat.completions.create(model = 'gpt-4',
        temperature=0,
        messages=message_objects)

        reply = response.choices[0].message.content

        return reply

def make_google_search(question:str):
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

    API_KEY = GOOGLE_API_KEY
    SEARCH_ENGINE_ID = GOOGLE_CSE_ID

    query = question
    page = 1
    start = (page - 1) * 5 + 1

    url = f"https://www.googleapis.com/customsearch/v1?key={API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}&start={start}"

    data = requests.get(url).json()

    search_items = data.get("items")

    GPTGoogleCosts = []
    textList = []
    for i, search_item in enumerate(search_items[:3], start=1):
        
        link = search_item.get("link")
                
        url = link
        start_time = time.time()
        
        response = requests.get(url, timeout=5)

        if time.time() - start_time < 5:
            soup = BeautifulSoup(response.content, 'html.parser')
            text = soup.get_text()
            text = text.replace("/n", "")
            text = text.replace("\n", "")
            text = text.replace("\\n", "")
            text = text.replace("//n", "")
            text = text.replace("//", "")
            text = text.replace("\t", "")
            text = text.replace("\t3", "")
            text = text.replace("\xa0", "")
            text = text.replace("  ", "")

            cost = round(num_tokens_from_string(text+str(question))*1.1)/1000*0.06
            GPTGoogleCosts.append(cost)
            textList.append(text[:4000])
        else:
            print(f"Skipping {link} as it took too long to get the data")        
    
    answer = getGPTAnswer(textList, question)
    costs = sum(GPTGoogleCosts)
    return {'answer':answer,'costs':costs}
    
question = 'What is the Jadu NFT and who is the CEO?'
result = make_google_search(question)

print(result)