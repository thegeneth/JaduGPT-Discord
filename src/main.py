import discord
from discord import Message as DiscordMessage
import logging
from src.constants import (
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
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
from src.completion import generate_completion_response, process_response
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import requests
import time
import psycopg2


load_dotenv()
postgrepw = str(os.getenv("POSTGREPW"))
postgrehost = str(os.getenv("POSTGREHOST"))


class POSTGRE_DB_HELPER:
    def get_postgre_connection(self) -> psycopg2.extensions.connection:
        conn = psycopg2.connect(
            database="postgres",
            user="postgres",
            password=postgrepw,
            host=postgrehost,
            port="5432",
        )
        return conn


def choose_model_for_user(user_id):
    user_id = str(user_id)
    skip_values = ["1104163607979249736", "1105175899743203358"]

    if str(user_id) not in skip_values:
        connection = POSTGRE_DB_HELPER().get_postgre_connection()
        cur = connection.cursor()

        # Get the current date and time
        current_date = datetime.now()

        # Calculate the start date for the previous week
        start_date = current_date - timedelta(days=1)

        # Generate the SQL query with explicit type cast
        query = f"SELECT SUM(cost::numeric) as TotalCost FROM jadugpt.costs WHERE userid = '{user_id}' AND datetime >= '{start_date}' AND datetime <= '{current_date}';"

        cur.execute(query)
        result = cur.fetchall()

        try:
            # Get the total cost from the result
            if result[0][0] is None:
                total_cost = 0
            else:
                total_cost = float(result[0][0])
            print(f"LOG: daily cost of user {user_id} is {total_cost}")
        except Exception as e:
            print(e)
            return "gpt-3.5-turbo"

        # Return the model
        if total_cost <= 0.999:
            return "gpt-4-turbo-preview"
        else:
            return "gpt-3.5-turbo"
    else:
        return "gpt-3.5-turbo"


def check_network_availability():
    url = "https://www.google.com"
    while True:
        try:
            response = requests.head(url, timeout=5)
            if response.status_code == 200:
                print("Network is available. Continuing...")
                # Your code to execute if the network is available
                break  # Exit the loop and continue with the rest of your code
            else:
                print("Unable to connect to Google. Retrying in 5 seconds...")
        except requests.conError:
            print("No network con. Retrying in 5 seconds...")
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
    await tree.sync()


# /chat create thread:
@tree.command(
    name="chat", description="create private thread for you to chat with Jadu-GPT"
)
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def thread_command(interaction: discord.Interaction):
    try:
        # only support creating thread in text channel
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        # block servers not in allow list
        if should_block(guild=interaction.guild):
            return

        con = POSTGRE_DB_HELPER().get_postgre_connection()

        cursor = con.cursor()
        user_id = str(interaction.user.id)
        sql = f"SELECT * FROM jadugpt.blockedusers WHERE blockeduserid = '{user_id}' AND isblocked = true"

        cursor.execute(sql)
        result = cursor.fetchall()

        sql2 = f"SELECT * FROM jadugpt.threads WHERE userid = '{user_id}'"

        cursor.execute(sql2)
        result2 = cursor.fetchall()

        def count_elements_less_than_10_minutes(tuple_list):
            current_time = datetime.now()
            count = 0

            for element in tuple_list:
                timestamp = datetime.strptime(element[0], "%Y-%m-%d %H:%M:%S.%f")
                time_difference = current_time - timestamp

                if time_difference.total_seconds() / 60 <= 10:
                    count += 1

                if element[2] == "allow" and time_difference.total_seconds() / 60 <= 10:
                    return 0

            return count

        previous_10min_threads = count_elements_less_than_10_minutes(result2)

        if previous_10min_threads <= 1:
            if len(result) == 0:
                user = interaction.user

                try:
                    embed = discord.Embed(
                        title="🤖💬 Jadu-GPT response will be sent on private thread!",
                        description=f"{interaction.user.mention} be sure not to spam! ",
                        color=discord.Color.green(),
                    )

                    await interaction.response.send_message(embed=embed)

                    # create the thread
                    thread = await interaction.channel.create_thread(
                        name=f"{ACTIVATE_THREAD_PREFX} {interaction.user.name[:20]}",
                        slowmode_delay=1,
                        reason="gpt-bot",
                        auto_archive_duration=60,
                        invitable=True,
                        type=None,
                    )

                    await thread.send(f"{interaction.user.mention}")

                    query = (
                        "INSERT INTO jadugpt.threads (date, userid) VALUES  (%s, %s)"
                    )

                    val = (str(datetime.now()), user_id)
                    cursor.execute(query, val)
                    con.commit()

                    embed = discord.Embed(
                        color=discord.Color.green(),
                        title=f"Be advised with instructions:",
                        description="",
                    )

                    embed.add_field(
                        name="⚠️ Be sure not to spam!",
                        value="We do not save your questions but we do monitor user interactions and costs. Base model is GPT-4 with 3.5 as a backup.",
                        inline=False,
                    )
                    embed.add_field(
                        name="✅ Start new /chat:",
                        value="Whenever you want to change the subject of your conversation, be sure to start a new thread with /chat at the <#1172653318842089584>",
                        inline=False,
                    )
                    embed.add_field(
                        name="👷 Ask for help",
                        value="You can ask for help from the team or from @Toven",
                        inline=False,
                    )
                    embed.add_field(
                        name="🚫 Our Restrictions",
                        value="We allow users to create up to 2 new threads every 10 minutes",
                        inline=False,
                    )

                    await thread.send(embed=embed)

                except Exception as e:
                    logger.exception(e)
                    await interaction.response.send_message(
                        f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
                        ephemeral=True,
                    )
                    return
        else:
            embed = discord.Embed(
                title="🚫 Limit reached ",
                description=f"{interaction.user.mention} Seems like you reached the limit of new threads. Please wait 10 minutes and try /chat again.",
                color=discord.Color.red(),
            )

            await interaction.response.send_message(embed=embed)

    except Exception as e:
        logger.exception(e)
        await interaction.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
            ephemeral=True,
        )


# /deny user:
@tree.command(name="deny", description="Deny UserID from using JaduGPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def deny_command(interaction: discord.Interaction, user: discord.User):
    try:
        # only support creating thread in text channel
        if not isinstance(interaction.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=interaction.guild):
            return

        userID = user.id

        con = POSTGRE_DB_HELPER().get_postgre_connection()

        cursor = con.cursor()

        sql = "INSERT INTO jadugpt.blockedusers (moderator, blockeduserid, datetime, isblocked) VALUES  (%s, %s, %s, %s)"

        val = (str(interaction.user), str(userID), str(datetime.now()), True)
        cursor.execute(sql, val)
        con.commit()

        try:
            await interaction.response.send_message(f"/deny by {interaction.user.mention}")

        except Exception as e:
            logger.exception(e)
            await interaction.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
                ephemeral=True,
            )
            return

        thread = interaction.channel

        await thread.send(f"{interaction.user.mention}" + " blocked UserID " + f'"{userID}"')

    except Exception as e:
        logger.exception(e)
        await interaction.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
            ephemeral=True,
        )


# /allow user:
@tree.command(name="allow", description="Allow UserID from using JaduGPT")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def allow_command(interaction: discord.Interaction, user: discord.User):
    try:
        # only support creating thread in text channel
        if not isinstance(interaction.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=interaction.guild):
            return

        userId = user.id

        con = POSTGRE_DB_HELPER().get_postgre_connection()

        cursor = con.cursor()

        sql = "UPDATE jadugpt.blockedusers SET isblocked = %s WHERE blockeduserid = %s"

        val = (False, str(user.id))
        cursor.execute(sql, val)
        con.commit()

        sql2 = f"SELECT * FROM jadugpt.threads WHERE userid = '{str(userId)}'"

        cursor.execute(sql2)
        result2 = cursor.fetchall()

        def get_most_recent_datetime(tuple_list):
            most_recent_datetime = None

            for element in tuple_list:
                timestamp = datetime.strptime(element[0], "%Y-%m-%d %H:%M:%S.%f")
                if most_recent_datetime is None or timestamp > most_recent_datetime:
                    most_recent_datetime = timestamp

            return most_recent_datetime

        most_recent_datetime = get_most_recent_datetime(result2)
    
        sql3 = "UPDATE jadugpt.threads SET allowed = 'allow' WHERE date = %s AND userid = %s"

        val = (str(most_recent_datetime), str(userId))
        cursor.execute(sql3, val)
        con.commit()

        try:
            await interaction.response.send_message(f"/allow by {interaction.user.mention}")

        except Exception as e:
            logger.exception(e)
            await interaction.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
                ephemeral=True,
            )
            return

        thread = interaction.channel

        await thread.send(f"{interaction.user.mention}" + " unblocked UserID " + f'"{userId}"')

    except Exception as e:
        logger.exception(e)
        await interaction.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
            ephemeral=True,
        )


# /costs all costs:
@tree.command(name="costs", description="request all costs by users")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def costs_command(interaction: discord.Interaction):
    try:
        # only support creating thread in text channel
        if not isinstance(interaction.channel, discord.Thread):
            return

        # block servers not in allow list
        if should_block(guild=interaction.guild):
            return

        con = POSTGRE_DB_HELPER().get_postgre_connection()

        cursor = con.cursor()

        sql = "SELECT DiscordUsername, UserID, SUM(cost::numeric) AS TotalCost FROM (SELECT discordusername, userid, cost::numeric FROM jadugpt.costs GROUP BY discordusername, userid, cost UNION ALL SELECT 'Grand Total', NULL, SUM(cost::numeric) AS TotalCost FROM jadugpt.costs) AS result GROUP BY DiscordUsername, UserID;"

        cursor.execute(sql)
        result = cursor.fetchall()

        try:
            await interaction.response.send_message(f"/costs by {interaction.user.mention}")

        except Exception as e:
            logger.exception(e)
            await interaction.response.send_message(
                f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
                ephemeral=True,
            )
            return

        thread = interaction.channel

        embed = discord.Embed(
            color=discord.Color.green(),
            title=f"These are the Costs for JaduGPT with Breakdown",
            description="",
        )

        sorted_data = sorted(result, key=lambda x: x[2], reverse=True)

        for item in sorted_data[0:21]:
            name_, userID, costs = item
            if str(name_).startswith("Grand Total"):
                embed.add_field(
                    name=str(str(name_)), value=str(round(costs, 4)), inline=False
                )
            else:
                embed.add_field(
                    name=str(str(name_) + " with UserID: " + str(userID)),
                    value=str(round(costs, 4)),
                    inline=False,
                )

        await thread.send(embed=embed)

    except Exception as e:
        logger.exception(e)
        await interaction.response.send_message(
            f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
            ephemeral=True,
        )


# calls for each message
@client.event
async def on_message(message: DiscordMessage):
    try:
        # ignore messages not in a thread
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            return
        # if the channel is a thread, check the thread parent channel id.
        if channel.parent_id != 1172653318842089584:
            return
        # ignore messages from the bot
        if message.author == client.user:
            return
        # block servers not in allow list
        if should_block(guild=message.guild):
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
        
        guild = discord.utils.get(client.guilds, id=669643831435722754)
        logchannel = guild.get_channel(1067344885977460787) or await guild.fetch_channel(
            1067344885977460787
        )

        con = POSTGRE_DB_HELPER().get_postgre_connection()

        cursor = con.cursor()
        user_id = str(message.author.id)

        sql = f"SELECT * FROM jadugpt.blockedusers WHERE blockeduserid = '{user_id}' AND isblocked = true"

        cursor.execute(sql)
        result = cursor.fetchall()
        
        if len(result) == 0:
            if str(message.content[0:2]) != "<@":
                if str(message.content[0:1]) != "/":
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
                        f"Thread message to process - {message.author}: {message.content[:30]} - {thread.name} {thread.jump_url}"
                    )

                    channel_messages = [
                        discord_message_to_message(message)
                        async for message in thread.history(limit=MAX_THREAD_MESSAGES)
                    ]
                    channel_messages = [x for x in channel_messages if x is not None]
                    channel_messages.reverse()

                    # generate the response
                    async with thread.typing():
                        response_data = await generate_completion_response(
                            messages=channel_messages,
                            user=message.author,
                            gptmodel=choose_model_for_user(message.author.id),
                            connection=con
                        )

                    # send response
                    await process_response(
                        user=message.author, thread=thread, response_data=response_data
                    )
        else:
            try:
                embed = discord.Embed(
                    title="🤖💬 Seems like you have been blocked from using /chat command.",
                    description=f"{message.author.mention} please contact moderators! ",
                    color=discord.Color.green(),
                )
                await logchannel.send(f"{message.author.mention} has been blocked from using /chat command in thread {thread.name} {thread.jump_url}")
                await message.channel.send(embed=embed)

            except Exception as e:
                logger.exception(e)
                await logchannel.send(f"Something went wrong trying to start chat with {message.author.mention} in thread {thread.name} {thread.jump_url}. Error message: {e}")
                await message.channel.send(
                    f"Failed to start chat, please try again. If the error continues reach out to moderators with specifications of when the error occured.",
                    ephemeral=True,
                )
                return

    except Exception as e:
        logger.exception(e)
        await logchannel.send(f"Something went wrong in on_message. Error message: {e}")


client.run(DISCORD_BOT_TOKEN)
