from enum import Enum
from dataclasses import dataclass
import openai
from openai import AsyncOpenAI
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
from dotenv import load_dotenv
from datetime import datetime
import os
import psycopg2


load_dotenv()
postgrepw = str(os.getenv("POSTGREPW"))
postgrehost = str(os.getenv("POSTGREHOST"))
systemprompt = str(os.getenv("SYSTEMPROMPT"))
encoding = tiktoken.encoding_for_model("gpt-4")

client = AsyncOpenAI()

conn = psycopg2.connect(
            database="postgres",
            user="postgres",
            password=postgrepw,
            host=postgrehost,
            port="5432",
        )
cur = conn.cursor()


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

async def generate_completion_response(
    messages: List[Message], user: str, gptmodel:str, sys_prompt:str = None
) -> CompletionData:
    conn = psycopg2.connect(
            database="postgres",
            user="postgres",
            password=postgrepw,
            host=postgrehost,
            port="5432",
        )
    cur = conn.cursor()
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
        if sys_prompt is not None:
            system_prompt = {
                "role": "system",
                "content": sys_prompt,
            }
        else:
            system_prompt = {
                "role": "system",
                "content": systemprompt,
            }
        message_objects.append(system_prompt)
        for message in messages:
            if message.text[0:2] != "<@":
                message_object = {"role": message.user, "content": str(message.text)}
                message_objects.append(message_object)
        for obj in message_objects:
            if obj["role"] == "JaduGPT":
                obj["role"] = "assistant"
            elif obj["role"] == "system":
                obj["role"] = "system"
            else:
                obj["role"] = "user"

        response = await client.chat.completions.create(
            model=gptmodel, temperature=0, messages=message_objects
        )

        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
    
        if gptmodel == 'gpt-3.5-turbo-1106':
            input_cost = prompt_tokens/1000*0.001
            output_cost = completion_tokens/1000*0.002
            cost = input_cost + output_cost
            print(f"LOG: model: {response.model}, user: {user}, cost: {'{:.4f}'.format(cost)}")
        else:
            input_cost = prompt_tokens/1000*0.01
            output_cost = completion_tokens/1000*0.03
            cost = input_cost + output_cost
            print(f"LOG: model: {response.model}, user: {user}, cost: {'{:.4f}'.format(cost)}")

        sql = "INSERT INTO jadugpt.costs VALUES (%s, %s, %s, %s)"
        val = (str(user), str(user.id), str(cost), str(datetime.now()))
        cur.execute(sql, val)
        conn.commit()

        reply = response.choices[0].message.content

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
    except openai.BadRequestError as e:
        if "This model's maximum context length" in e.message:
            return CompletionData(
                status=CompletionResult.TOO_LONG,
                reply_text=None,
                status_text="Failed to complete the chat, it seems text were too long. Please try again in a new chat. If the error continues reach out to moderators with specifications of when the error occured.",
            )
        else:
            logger.exception(e)
            return CompletionData(
                status=CompletionResult.INVALID_REQUEST,
                reply_text=None,
                status_text="Oops, an error occured while processing your request. Please try again in a new chat, if error persist please reach out to moderators. You can add them to the Thread by mentioning them with @.",
            )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            # status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
            status=CompletionResult.OTHER_ERROR,
            reply_text=None,
            status_text="Oops, an error occured while processing your request. Please try again and if error persist, please reach out to moderators. You can add them to the Thread by mentioning them with @.",
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
