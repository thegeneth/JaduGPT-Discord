import discord
from discord import Message as DiscordMessage
import logging
from src.base import Message, Conversation
from src.constants import (
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
    EXAMPLE_CONVOS,
    ACTIVATE_THREAD_PREFX,
    MAX_THREAD_MESSAGES,
    SECONDS_DELAY_RECEIVING_MSG,
)
import asyncio
from src.utils import (
    logger,
    should_block,
    close_thread,
    is_last_message_stale,
    discord_message_to_message,
)
from src import completion
from src.completion import generate_completion_response, process_response,generate_summary

from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)
from datetime import datetime
import pandas as pd
from datetime import datetime, timedelta

import tiktoken
import os
from dotenv import load_dotenv
from mysql.connector import Error
import mysql.connector
import MySQLdb
from datetime import datetime
import json
import requests
import time

def check_network_availability():
    url = 'https://www.google.com'
    while True:
        try:
            response = requests.head(url, timeout=5)
            if response.status_code == 200:
                print("Network is available. Continuing...")
                # Your code to execute if the network is available
                break  # Exit the loop and continue with the rest of your code
            else:
                print("Unable to connect to Google. Retrying in 5 seconds...")
        except requests.ConnectionError:
            print("No network connection. Retrying in 5 seconds...")
        time.sleep(5)  # Wait for 5 seconds before retrying

check_network_availability()

logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


@client.event
async def on_ready():
    logger.info(f"We have logged in as {client.user}. Invite URL: {BOT_INVITE_URL}")
    completion.MY_BOT_NAME = client.user.name
    completion.MY_BOT_EXAMPLE_CONVOS = []
    for c in EXAMPLE_CONVOS:
        messages = []
        for m in c.messages:
            if m.user == "Lenard":
                messages.append(Message(user=client.user.name, text=m.text))
            else:
                messages.append(m)
        completion.MY_BOT_EXAMPLE_CONVOS.append(Conversation(messages=messages))
    await tree.sync()

# /chat message:
@tree.command(name="google", description="Create a new thread starting with a google search by GPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def chat_command(int: discord.Interaction, message: str):
    try:
        # # only support creating thread in text channel
        # if not isinstance(int.channel, discord.TextChannel):
        #     return
        # ignore messages not in a thread
                
        if not isinstance(int.channel, discord.Thread):
            return
        thread =  int.channel

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
        
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()
        
        sql = f"SELECT * FROM JaduBlockedUsers WHERE BlockedUserID = {int.user.id} AND IsBlocked = 1"

        mycursor.execute(sql)
        result = mycursor.fetchall()

        channel_messages = [
                    discord_message_to_message(message)
                    async for message in thread.history(limit=MAX_THREAD_MESSAGES)
                ]
        channel_messages = [x for x in channel_messages if x is not None]
        channel_messages.reverse()

        google_messages_count = sum(message.text.startswith('/google') for message in channel_messages)
        print(google_messages_count)

        if google_messages_count < 2:
            if len(result) == 0:
                user = int.user
                logger.info(f"Chat command by {user} {message[:20]}")

                try:
                    # moderate the message
                    flagged_str, blocked_str = moderate_message(message=message, user=user)
                    await send_moderation_blocked_message(
                        guild=int.guild,
                        user=user,
                        blocked_str=blocked_str,
                        message=message,
                    )
                    if len(blocked_str) > 0:
                        # message was blocked
                        await int.response.send_message(
                            f"Your prompt has been blocked by moderation.\n{message}",
                            ephemeral=True,
                        )
                        return

                    await int.response.send_message(f'/google by {int.user.mention}')
                    response = await int.original_response()

                    await send_moderation_flagged_message(
                        guild=int.guild,
                        user=user,
                        flagged_str=flagged_str,
                        message=message,
                        url=response.jump_url,
                    )
                    
                except Exception as e:
                    logger.exception(e)
                    await int.response.send_message(
                        f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
                    )
                    return

                async with thread.typing():
                    # fetch completion
                    messages = [Message(user=user.name, text=message)]
                    response_data = await generate_summary(
                        messages=messages, user=user
                    )
                    # send the result
                    await process_response(
                        user=user, thread=thread, response_data=response_data
                    )
            else:
                try:
                    embed = discord.Embed(
                        title='🤖💬 Seems like you have been blocked from using /chat command.',
                        description=f"{int.user.mention} please contact moderators! ",
                        color=discord.Color.green()
                    )

                    await int.response.send_message(embed=embed)

                except Exception as e:
                    logger.exception(e)
                    await int.response.send_message(
                        f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
                    )
                    return
        else:
            try:
                embed = discord.Embed(
                    title='Seems like you have reached the limit of the 2 /google commands on this thread.',
                    description=f"You can continue the conversation with GPT, but if you want to make another /google search, be sure to start a new thread with /chat at the <#1105175304395309066>",
                    color=discord.Color.green()
                )

                await int.response.send_message(embed=embed)

            except Exception as e:
                logger.exception(e)
                await int.response.send_message(
                    f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
                )
                return

    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
        )


# /costs all costs:
@tree.command(name="chat", description="create private thread for you to chat with JaduGPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def thread_command(int: discord.Interaction):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.TextChannel):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
                
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()
        
        sql = f"SELECT * FROM JaduBlockedUsers WHERE BlockedUserID = {int.user.id} AND IsBlocked = 1"

        mycursor.execute(sql)
        result = mycursor.fetchall()

        sql2 = f"SELECT * FROM JaduThreads WHERE UserID = {int.user.id}"

        mycursor.execute(sql2)
        result2 = mycursor.fetchall()
        # Fetch column names from cursor.description
        # column_names = [column[0] for column in mycursor.description]

        # Convert the results into a list of dictionaries
        # data = [dict(zip(column_names, row)) for row in result2]

        # Convert the list of dictionaries into JSON
        # json_data = json.dumps(data)

        # Now json_data contains the result in JSON format
        # print(json_data)
        # print(result2)
        # print(type(result2))
        
        # df = pd.DataFrame(result2)
        # print(df)
        # df['Date'] = pd.to_datetime(df['Date'])
        # now = datetime.now()
        # ten_minutes_ago = now - timedelta(minutes=10)
        # filtered_df = df[df['Date'] > ten_minutes_ago]

        
        if len(result) == 0:
            user = int.user

            try:
                
                embed = discord.Embed(
                    title='🤖💬 JaduGPT response will be sent on private thread!',
                    description=f"{int.user.mention} be sure not to spam! ",
                    color=discord.Color.green()
                )           

                await int.response.send_message(embed=embed)
            
            except Exception as e:
                logger.exception(e)
                await int.response.send_message(
                    f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
                )
                return

        # create the thread
        thread = await int.channel.create_thread(
            name=f"{ACTIVATE_THREAD_PREFX} {int.user.name[:20]}",
            slowmode_delay=1,
            reason="gpt-bot",
            auto_archive_duration=60,
            invitable=True,
            type=None
        )

        await thread.send(f"{int.user.mention}")

        query = "INSERT INTO JaduThreads (Date, UserID) VALUES  (%s, %s)"
    
        val = ({str(datetime.now())}, {str(int.user.id)})
        mycursor.execute(query, val)
        connection.commit()
        connection.close()

        embed = discord.Embed(
                        color=discord.Color.green(),
                        title=f"Be advised with instructions:",
                        description=''
                    )
            
        embed.add_field(name='⚠️ Be sure not to spam!', value='We do not save your questions but we do monitor user interactions and costs', inline=False)
        embed.add_field(name='✅ Start new /chat:', value='Whenever you want to change the subject of your conversation, be sure to start a new thread with /chat at the <#1105175304395309066>', inline=False)
        embed.add_field(name='🔎 Use /google:', value='You can use /google to make the GPT do a Google Search to update its knowledge base according to your message. This feature is limited in 2 / thread.', inline=False)
        embed.add_field(name='👷 Ask for help:', value='You can ask for help from the team or from @thegen (the project dev)', inline=False)
        #embed.add_field(name='🚫 Our Restrictions:', value='We allow users to create up to 2 new threads every 10 minutes', inline=False)

        await thread.send(embed=embed)


        # if len(filtered_df) <= 2:
           
        #     embed = discord.Embed(
        #                 color=discord.Color.green(),
        #                 title=f"Be advised with instructions:",
        #                 description=''
        #             )
            
        #     embed.add_field(name='⚠️ Be sure not to spam!', value='We do not save your questions but we do monitor user interactions and costs', inline=False)
        #     embed.add_field(name='✅ Start new /chat:', value='Whenever you want to change the subject of your conversation, be sure to start a new thread with /chat at the <#1105175304395309066>', inline=False)
        #     embed.add_field(name='🔎 Use /google:', value='You can use /google to make the GPT do a Google Search to update its knowledge base according to your message. This feature is limited in 2 / thread.', inline=False)
        #     embed.add_field(name='👷 Ask for help:', value='You can ask for help from the team or from @thegen (the project dev)', inline=False)
        #     embed.add_field(name='🚫 Our Restrictions:', value='We allow users to create up to 2 new threads every 10 minutes', inline=False)

        #     await thread.send(embed=embed)

        # else:
        #     embed = discord.Embed(
        #                 color=discord.Color.green(),
        #                 title=f"Be advised with instructions:",
        #                 description=''
        #             )
        #     embed.add_field(name='🚫 Our Restrictions:', value='We allow users to create up to 2 new threads for every 10 minutes', inline=False)
        #     embed.add_field(name='⚠️ Seems like you reached the limit!', value='We do not save your questions but we do monitor user interactions and costs.', inline=False)
        #     embed.add_field(name='✅ Please wait to start a new /chat:', value='It seems that you have reaced our limits for new threads. Please wait 10 more minutes and try /chat again at <#1105175304395309066>', inline=False)
            
        #     await thread.send(embed=embed)

        
    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
        )


# /deny user:
@tree.command(name="deny", description="Deny UserID from using JaduGPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def deny_command(int: discord.Interaction, message: str):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
                
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()

        sql = "INSERT INTO JaduBlockedUsers (Moderator, BlockedUserID, DateTime, IsBlocked) VALUES  (%s, %s,%s, %s)"
        
        val = ({str(int.user)}, {str(message)}, {str(datetime.now())}, 1)
        mycursor.execute(sql, val)
        connection.commit()
        connection.close()

        try:

            await int.response.send_message(f'/deny by {int.user.mention}')
            
        except Exception as e:
            logger.exception(e)
            await int.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
            )
            return
        
      
        thread =  int.channel

        await thread.send(f"{int.user.mention}" + " blocked UserID "+ f'"{message}"')

    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
        )

# /allow user:
@tree.command(name="allow", description="Allow UserID from using JaduGPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def allow_command(int: discord.Interaction, message: str):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
                
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()

        sql = "UPDATE JaduBlockedUsers SET IsBlocked = 0 WHERE BlockedUserID = %s"
        
        val = ({str(message)})
        mycursor.execute(sql, val)
        connection.commit()
        connection.close()

        try:

            await int.response.send_message(f'/allow by {int.user.mention}')
            
        except Exception as e:
            logger.exception(e)
            await int.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
            )
            return
        
      
        thread =  int.channel

        await thread.send(f"{int.user.mention}" + " unblocked UserID "+ f'"{message}"')

    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
        )

# /costs all costs:
@tree.command(name="costs", description="request all costs by users")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def allow_command(int: discord.Interaction):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
                
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()

        sql = "SELECT User, UserID, TotalCost FROM (SELECT User, UserID, SUM(Cost) AS TotalCost FROM JaduGPT GROUP BY User, UserID UNION ALL SELECT 'Grand Total', NULL, SUM(Cost) AS TotalCost FROM JaduGPT) AS result;"
                
        mycursor.execute(sql)
        result = mycursor.fetchall()
        connection.commit()
        connection.close()

        try:

            await int.response.send_message(f'/costs by {int.user.mention}')
            
        except Exception as e:
            logger.exception(e)
            await int.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
            )
            return
      
        thread =  int.channel

        embed = discord.Embed(
                    color=discord.Color.green(),
                    title=f"These are the Costs for JaduGPT with Breakdown",
                    description=''
                )       

        for item in result:
            name_, userID, costs = item
            if str(name_).startswith('Grand Total'):
                embed.add_field(name=str(str(name_)), value=str(round(costs, 4)), inline=False)
            else:
                embed.add_field(name=str(str(name_)+' with UserID: '+str(userID)), value=str(round(costs, 4)), inline=False)

        await thread.send(embed=embed)

    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
        )

# calls for each message
@client.event
async def on_message(message: DiscordMessage):
    try:
        connection = MySQLdb.connect(
            host= os.getenv("HOST"),
            user=os.getenv("USERNAME2"),
            password= os.getenv("PASSWORD"),
            db= os.getenv("DATABASE"),
            ssl=os.getenv("SSL_CERT")
        )

        mycursor = connection.cursor()
        
        sql = f"SELECT * FROM JaduBlockedUsers WHERE BlockedUserID = {message.author.id} AND IsBlocked = 1"

        mycursor.execute(sql)
        result = mycursor.fetchall()
        connection.commit()
        connection.close()

        if len(result) == 0:
            if message.content[0:1] != '<@':
                # block servers not in allow list
                if should_block(guild=message.guild):
                    return

                # ignore messages from the bot
                if message.author == client.user:
                    return

                # ignore messages not in a thread
                channel = message.channel
                if not isinstance(channel, discord.Thread):
                    return

                # ignore threads not created by the bot
                thread = channel
                if thread.owner_id != client.user.id:
                    return

                # ignore threads that are archived locked or title is not what we want
                if (
                    thread.archived
                    or thread.locked
                    or not thread.name.startswith(ACTIVATE_THREAD_PREFX)
                ):
                    # ignore this thread
                    return

                if thread.message_count > MAX_THREAD_MESSAGES:
                    # too many messages, no longer going to reply
                    await close_thread(thread=thread)
                    return

                # moderate the message
                flagged_str, blocked_str = moderate_message(
                    message=message.content, user=message.author
                )
                await send_moderation_blocked_message(
                    guild=message.guild,
                    user=message.author,
                    blocked_str=blocked_str,
                    message=message.content,
                )
                if len(blocked_str) > 0:
                    try:
                        await message.delete()
                        await thread.send(
                            embed=discord.Embed(
                                description=f"❌ **{message.author}'s message has been deleted by moderation.**",
                                color=discord.Color.red(),
                            )
                        )
                        return
                    except Exception as e:
                        await thread.send(
                            embed=discord.Embed(
                                description=f"❌ **{message.author}'s message has been blocked by moderation but could not be deleted. Missing Manage Messages permission in this Channel.**",
                                color=discord.Color.red(),
                            )
                        )
                        return
                await send_moderation_flagged_message(
                    guild=message.guild,
                    user=message.author,
                    flagged_str=flagged_str,
                    message=message.content,
                    url=message.jump_url,
                )
                if len(flagged_str) > 0:
                    await thread.send(
                        embed=discord.Embed(
                            description=f"⚠️ **{message.author}'s message has been flagged by moderation.**",
                            color=discord.Color.yellow(),
                        )
                    )

                # wait a bit in case user has more messages
                if SECONDS_DELAY_RECEIVING_MSG > 0:
                    await asyncio.sleep(SECONDS_DELAY_RECEIVING_MSG)
                    if is_last_message_stale(
                        interaction_message=message,
                        last_message=thread.last_message,
                        bot_id=client.user.id,
                    ):
                        # there is another message, so ignore this one
                        return

                logger.info(
                    f"Thread message to process - {message.author}: {message.content[:50]} - {thread.name} {thread.jump_url}"
                )

                channel_messages = [
                    discord_message_to_message(message)
                    async for message in thread.history(limit=MAX_THREAD_MESSAGES)
                ]
                channel_messages = [x for x in channel_messages if x is not None]
                channel_messages.reverse()

                print(thread.name)
                if thread.name[0:3] == 'GPT4':
                    # generate the response
                    async with thread.typing():
                        response_data = await generate_completion_response4(
                            messages=channel_messages, user=message.author
                        )

                    if is_last_message_stale(
                        interaction_message=message,
                        last_message=thread.last_message,
                        bot_id=client.user.id,
                    ):
                        # there is another message and its not from us, so ignore this response
                        return

                    # send response
                    await process_response4(
                        user=message.author, thread=thread, response_data=response_data
                    )


                # generate the response
                async with thread.typing():
                    response_data = await generate_completion_response(
                        messages=channel_messages, user=message.author
                    )

                if is_last_message_stale(
                    interaction_message=message,
                    last_message=thread.last_message,
                    bot_id=client.user.id,
                ):
                    # there is another message and its not from us, so ignore this response
                    return

                # send response
                await process_response(
                    user=message.author, thread=thread, response_data=response_data
                )
        else:
            try:
                embed = discord.Embed(
                    title='🤖💬 Seems like you have been blocked from using /chat command.',
                    description=f"{message.author.mention} please contact moderators! ",
                    color=discord.Color.green()
                )

                await message.channel.send(embed=embed)

            except Exception as e:
                logger.exception(e)
                await message.channel.send(
                    f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.", ephemeral=True
                )
                return
            
    except Exception as e:
        logger.exception(e)


client.run(DISCORD_BOT_TOKEN)
