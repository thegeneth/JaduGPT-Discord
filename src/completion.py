from enum import Enum
from dataclasses import dataclass
import openai
from src.moderation import moderate_message
from typing import Optional, List
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_NAME,
    EXAMPLE_CONVOS,
)
import discord
from src.base import Message, Prompt, Conversation
from src.utils import split_into_shorter_messages, close_thread, logger
from src.moderation import (
    send_moderation_flagged_message,
    send_moderation_blocked_message,
)
import tiktoken
import os
from dotenv import load_dotenv
from mysql.connector import Error
import mysql.connector
import MySQLdb
from datetime import datetime

import requests
from bs4 import BeautifulSoup



load_dotenv()



encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")

def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
    num_tokens = len(encoding.encode(string))
    return num_tokens

MY_BOT_NAME = BOT_NAME
MY_BOT_EXAMPLE_CONVOS = EXAMPLE_CONVOS


class CompletionResult(Enum):
    OK = 0
    TOO_LONG = 1
    INVALID_REQUEST = 2
    OTHER_ERROR = 3
    MODERATION_FLAGGED = 4
    MODERATION_BLOCKED = 5


@dataclass
class CompletionData:
    status: CompletionResult
    reply_text: Optional[str]
    status_text: Optional[str]

async def generate_summary(
    messages: List[Message], user: str
) -> CompletionData:
    try:
        prompt = Prompt(
            header=Message(
                "System", f"Instructions for {MY_BOT_NAME}: {BOT_INSTRUCTIONS}"
            ),
            examples=MY_BOT_EXAMPLE_CONVOS,
            convo=Conversation(messages + [Message(MY_BOT_NAME)]),
        )
        rendered = prompt.render()
        
        question = ''
        for message in messages:
            question = str(message.text)
        message_objects = []
        system_prompt = {"role": 'system', "content": f'You must try to answer this question "{question}" with content from the following texts.'}
        message_objects.append(system_prompt)
                

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
            response = requests.get(url)

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

            cost = round(num_tokens_from_string(text+str(question))*1.1)/1000*0.002
            GPTGoogleCosts.append(cost)
            textList.append(text[:4000])

        for prompt in textList:
            message_objects.append({"role": 'system', "content": f'{prompt}'})

        token_list = []
        for message in messages:
            if message.text[0:1] != '<@':
                message_object = {"role": message.user, "content": str(message.text)}
                message_objects.append(message_object)
        for obj in message_objects:
            token_list.append(num_tokens_from_string(obj['content']))
            if obj['role'] == 'JaduGPT':
                obj['role'] = 'assistant'
            elif obj['role'] == 'system':
                obj['role'] = 'system'
            else:
                obj['role'] = 'user'
        
        response = openai.ChatCompletion.create(
            model = 'gpt-3.5-turbo',
            temperature=0,
            messages=message_objects
        )

        reply = response.choices[0]["message"]["content"]

        costs = sum(GPTGoogleCosts)+0.01
        
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()

        sql = "INSERT INTO JaduGPT (User, UserID, Cost, Datetime) VALUES (%s, %s,%s, %s)"
        val = (str(user),str(user.id), str(costs), str(datetime.now()))
        mycursor.execute(sql, val)

        connection.commit()
        connection.close()

        if reply:
            flagged_str, blocked_str = moderate_message(
                message=(rendered + reply)[-500:], user=user
            )
            if len(blocked_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_BLOCKED,
                    reply_text=reply,
                    status_text=f"from_response:{blocked_str}",
                )

            if len(flagged_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_FLAGGED,
                    reply_text=reply,
                     status_text=f"from_response:{flagged_str}",
                )

        return CompletionData(
            status=CompletionResult.OK, reply_text=reply, status_text=None
        )

    except openai.error.InvalidRequestError as e:
        if "This model's maximum context length" in e.user_message:
            return CompletionData(
                status=CompletionResult.TOO_LONG, reply_text=None, status_text=e
            )
        else:
            logger.exception(e)
            return CompletionData(
                status=CompletionResult.INVALID_REQUEST,
                reply_text=None,
                status_text=e,
            )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            # status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
            status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=e
        )


async def generate_completion_response(
    messages: List[Message], user: str
) -> CompletionData:
    try:
        prompt = Prompt(
            header=Message(
                "System", f"Instructions for {MY_BOT_NAME}: {BOT_INSTRUCTIONS}"
            ),
            examples=MY_BOT_EXAMPLE_CONVOS,
            convo=Conversation(messages + [Message(MY_BOT_NAME)]),
        )
        rendered = prompt.render()
        message_objects = []
        system_prompt = {"role": 'system', "content": 'You are JaduGPT, a model just like ChatGPT but exclusive for Jadu NFT holders. Jadu is a collection of NFTs including a Jetpack, Hoverboard and Avatars. This project were created as a grant program lead by Thegen and voted by Jadu Community.'}
        message_objects.append(system_prompt)
        token_list = []
        for message in messages:
            if message.text[0:2] != '<@':
                message_object = {"role": message.user, "content": str(message.text)}
                message_objects.append(message_object)
        for obj in message_objects:
            token_list.append(num_tokens_from_string(obj['content']))
            if obj['role'] == 'JaduGPT':
                obj['role'] = 'assistant'
            elif obj['role'] == 'system':
                obj['role'] = 'system'
            else:
                obj['role'] = 'user'
        
        response = openai.ChatCompletion.create(
            model = 'gpt-3.5-turbo',
            temperature=0,
            messages=message_objects
        )
        reply = response.choices[0]["message"]["content"]
        
        token_list.append(num_tokens_from_string(reply))
        
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()

        tokenSum = round(sum(token_list)*1.1)

        cost = tokenSum/1000*0.002

        sql = "INSERT INTO JaduGPT (User, UserID, Cost, Datetime) VALUES (%s, %s,%s, %s)"
        val = (str(user),str(user.id), str(cost), str(datetime.now()))
        mycursor.execute(sql, val)

        connection.commit()
        connection.close()

        if reply:
            flagged_str, blocked_str = moderate_message(
                message=(rendered + reply)[-500:], user=user
            )
            if len(blocked_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_BLOCKED,
                    reply_text=reply,
                    status_text=f"from_response:{blocked_str}",
                )

            if len(flagged_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_FLAGGED,
                    reply_text=reply,
                    status_text=f"from_response:{flagged_str}",
                )

        return CompletionData(
            status=CompletionResult.OK, reply_text=reply, status_text=None
        )
    except openai.error.InvalidRequestError as e:
        if "This model's maximum context length" in e.user_message:
            return CompletionData(
                status=CompletionResult.TOO_LONG, reply_text=None, status_text="Failed to complete the chat, it seems text were too long. Please try again in a new chat. If the error continues reach out to moderators with specifications of when the error occured."
            )
        else:
            logger.exception(e)
            return CompletionData(
                status=CompletionResult.INVALID_REQUEST,
                reply_text=None,
                status_text='Oops, an error occured while processing your request. Please try again in a new chat, if error persist please reach out to moderators. You can add them to the Thread by mentioning them with @.',
            )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            # status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
            status=CompletionResult.OTHER_ERROR, reply_text=None, status_text='Oops, an error occured while processing your request. Please try again and if error persist, please reach out to moderators. You can add them to the Thread by mentioning them with @.'
        )


async def process_response(
    user: str, thread: discord.Thread, response_data: CompletionData
):
    status = response_data.status
    reply_text = response_data.reply_text
    status_text = response_data.status_text
    
    
    if status is CompletionResult.OK or status is CompletionResult.MODERATION_FLAGGED:
        sent_message = None
        if not reply_text:
            sent_message = await thread.send(
                embed=discord.Embed(
                    description=f"**Invalid response** - empty response",
                    color=discord.Color.yellow(),
                )
            )
        else:
            shorter_response = split_into_shorter_messages(reply_text)
            for r in shorter_response:
                sent_message = await thread.send(r)
        if status is CompletionResult.MODERATION_FLAGGED:
            await send_moderation_flagged_message(
                guild=thread.guild,
                user=user,
                flagged_str=status_text,
                message=reply_text,
                url=sent_message.jump_url if sent_message else "no url",
            )

            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **This conversation has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )
    elif status is CompletionResult.MODERATION_BLOCKED:
        await send_moderation_blocked_message(
            guild=thread.guild,
            user=user,
            blocked_str=status_text,
            message=reply_text,
        )

        await thread.send(
            embed=discord.Embed(
                description=f"❌ **The response has been blocked by moderation.**",
                color=discord.Color.red(),
            )
        )
    elif status is CompletionResult.TOO_LONG:
        await close_thread(thread)
    elif status is CompletionResult.INVALID_REQUEST:
        await thread.send(
            embed=discord.Embed(
                # description=f"**Invalid request** - {status_text}",
                description=f"**Invalid request** - Oops, an error occured while processing your request. Please try again and if error persist, please reach out to moderators. You can add them to the Thread by mentioning them with @.",
                color=discord.Color.yellow(),
            )
        )
    else:
        await thread.send(
            embed=discord.Embed(
                # description=f"**Error** - {status_text}",
                description=f"**Error** - Oops, an error occured while processing your request. Please try again and if error persist, please reach out to moderators. You can add them to the Thread by mentioning them with @.",
                color=discord.Color.yellow(),
            )
        )
