from enum import Enum
from dataclasses import dataclass
import openai
from openai import AsyncOpenAI
from typing import Optional, List
import discord
from src.base import Message
from src.utils import split_into_shorter_messages, close_thread, logger
import tiktoken
from dotenv import load_dotenv
from datetime import datetime
import os
import psycopg2
from anthropic import AsyncAnthropic


load_dotenv()
postgrepw = str(os.getenv("POSTGREPW"))
postgrehost = str(os.getenv("POSTGREHOST"))
systemprompt = str(os.getenv("SYSTEMPROMPT"))
encoding = tiktoken.encoding_for_model("gpt-4")

oai_client = AsyncOpenAI()
anthropic_client = AsyncAnthropic()

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
    messages: List[Message], user: str, gptmodel: str, connection: psycopg2.extensions.connection, 
) -> CompletionData:
    try:
        message_objects = []
        system_prompt = {
            "role": "system",
            "content": systemprompt,
        }
        if gptmodel.lower() != "claude":
            message_objects.append(system_prompt)
        for message in messages:
            if message.text[0:2] != "<@":
                message_object = {"role": message.user, "content": str(message.text)}
                message_objects.append(message_object)
        for obj in message_objects:
            if obj["role"] == "Jadu-GPT":
                obj["role"] = "assistant"
            elif obj["role"] == "TestBloom":
                obj["role"] = "assistant"
            else:
                obj["role"] = "user"

        if gptmodel.lower() == "claude":
            response = await anthropic_client.messages.create(
                model="claude-3-5-sonnet-20240620",
                messages=message_objects,
                system=systemprompt,  # Add system prompt as a separate parameter
                max_tokens=4096
            )
            reply = response.content[0].text
            prompt_tokens = response.usage.input_tokens
            completion_tokens = response.usage.output_tokens
            input_cost = prompt_tokens / 1000000 * 3  # $3 per million tokens of input
            output_cost = completion_tokens / 1000000 * 15  # $15 per million tokens of output
            cost = input_cost + output_cost
        else:
            response = await oai_client.chat.completions.create(
                model=gptmodel, temperature=0, messages=message_objects
            )
            reply = response.choices[0].message.content
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

            if gptmodel == "gpt-3.5-turbo":
                input_cost = prompt_tokens / 1000 * 0.0005
                output_cost = completion_tokens / 1000 * 0.0015
                cost = input_cost + output_cost
            else:
                input_cost = prompt_tokens / 1000 * 0.01
                output_cost = completion_tokens / 1000 * 0.03
                cost = input_cost + output_cost

        print(
            f"LOG: model: {gptmodel}, user: {user}, cost: {'{:.4f}'.format(cost)}"
        )

        cur = connection.cursor()
        sql = "INSERT INTO jadugpt.costs VALUES (%s, %s, %s, %s)"
        val = (str(user), str(user.id), str(cost), str(datetime.now()))
        cur.execute(sql, val)
        connection.commit()

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

    if status is CompletionResult.OK:
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
