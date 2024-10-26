import discord
import logging
import requests
import sqlite3
import yaml
import os
from discord.ext import tasks

logging.getLogger().setLevel(logging.INFO)

config = yaml.safe_load(open('config.yaml'))

idtoid = {}  # Create dict for username / discord id lookup

for i in range(len(config['coop']['minecraftnames'])):
    idtoid[config['coop']['discordids'][i]] = config['coop']['minecraftnames'][i]
    idtoid[config['coop']['minecraftnames'][i]] = config['coop']['discordids'][i]


def resetdatabase():
    try:
        os.makedirs('./data/')
    except FileExistsError:
        logging.info('Folder already exists')
    conn = sqlite3.connect('./data/database.db')
    curs = conn.cursor()
    curs.execute('''
            CREATE TABLE "history" (
                "index"	INTEGER UNIQUE,
                "amount"    NUMERIC,
                "timestamp"	INTEGER UNIQUE,
                "action"    TEXT,
                "initiator"	TEXT,
                PRIMARY KEY("index" AUTOINCREMENT)
            );
            ''')
    curs.execute('''
    CREATE TABLE "transferred" (
                "index"	INTEGER UNIQUE,
                "removefrom"    TEXT,
                "addto"	TEXT,
                "amount"    INTEGER,
                "reason"    TEXT,
                PRIMARY KEY("index" AUTOINCREMENT)
            );
            ''')
    conn.commit()


try:
    con = sqlite3.connect('./data/database.db')
except sqlite3.OperationalError:
    logging.error('Encountered error while checking database')
    reset = input('Reset database? (y/n)\n')
    if reset == 'y':
        resetdatabase()
    else:
        exit()
else:
    try:
        cur = con.cursor()
        cur.execute('SELECT * FROM history, transferred')
    except sqlite3.OperationalError:
        resetdatabase()


class HyDebtBot(discord.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logging.info('Starting GET task')
        self.hypixel_getdata.start()

    async def on_ready(self):
        logging.info(f'Logged in as {self.user} (ID: {self.user.id})')
        self.remindofdebt.start()

    @tasks.loop(minutes=5)
    async def hypixel_getdata(self):
        url = 'https://api.hypixel.net/v2/skyblock/profile'
        query = {'profile': config['coop']['profileid']}
        header = {
            'API-KEY': config['keys']['hypixel']
        }

        response = requests.request('GET', url, data='', headers=header, params=query)
        if response.status_code == 200:
            response = response.json()
        else:
            logging.error(f'Got {response.status_code} as response')
            exit()

        with open('./data/balance.txt', 'w') as f:
            balance = float(response['profile']['banking']['balance'])
            f.write('%.1f' % balance)

        tran = response['profile']['banking']['transactions']
        for x in range(len(tran)):
            if not tran[x]['initiator_name'] == 'Bank Interest':
                sqlcode = f'''
                INSERT OR IGNORE INTO history (amount,timestamp,action,initiator)
                VALUES(
                {tran[x]['amount']},
                {tran[x]['timestamp']}, 
                '{tran[x]['action']}', 
                '{tran[x]['initiator_name'][2:]}'
                 )
                '''
                cur.execute(sqlcode)
        con.commit()

    @tasks.loop(hours=24)
    async def remindofdebt(self):
        channel = self.get_channel(config['customization']['reminderchannel'])
        embed = discord.Embed(title="Its debt checking time!", color=config['customization']['embedcolor'])
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1178301279827673169.gif")
        debtors = "<a:peepobell:1178301279827673169>  "
        for x in range(len(config['coop']['minecraftnames'])):
            userbalance = gettotaldebt(config['coop']['minecraftnames'][x])
            if userbalance < 0:
                if not config['coop']['discordids'][x] == '':
                    debtors = debtors + "<@" + str(config['coop']['discordids'][x]) + "> "
                embed.add_field(name="", value="", inline=False)
                embed.add_field(name=config['coop']['minecraftnames'][x],
                                value="Is " + f'{-userbalance:,}' + " coins in debt!", inline=True)
        debtors = debtors + "<a:peepobell:1178301279827673169>"
        if not debtors == "<a:peepobell:1178301279827673169>  <a:peepobell:1178301279827673169>":
            await channel.send(debtors, embed=embed)
        else:
            embed.clear_fields()
            embed.add_field(name="Congratulations! None of you are in debt!",
                            value="Here's a cookie <a:boostercookie:1180650988584058910> ")
            await channel.send(embed=embed)

    @remindofdebt.before_loop
    async def before_reminder(self):
        await self.wait_until_ready()


bot = HyDebtBot(intents=discord.Intents.default())


# /// reused Functions ///

def getlasttransaction(user: str = ''):
    if not user == '':
        user = f'WHERE initiator == "{user}"'
    lasttransaction = cur.execute(
        f'SELECT "amount", "timestamp", "action", "initiator" FROM history {user} ORDER BY "index" DESC'
    )
    if lasttransaction.fetchone() is None:
        return 0, 0, 'NONE', 'USER'
    else:
        lasttransaction = lasttransaction.fetchone()
        return lasttransaction[0], lasttransaction[1], lasttransaction[2], lasttransaction[3]


def gettotaldebt(user: str = ''):
    totalbank = 0
    totaltransfer = 0

    sqlbank = f'SELECT action, amount FROM history WHERE initiator == "{user}"'
    sqltransfer = f'''
    SELECT removefrom, addto, amount
    FROM transferred 
    WHERE removefrom == "{user}" OR addto == "{user}"
    '''

    transactions = cur.execute(sqlbank)
    transactions = transactions.fetchall()

    transfers = cur.execute(sqltransfer)
    transfers = transfers.fetchall()

    for x in range(len(transactions)):
        if transactions[x][0] == 'DEPOSIT':
            totalbank += transactions[x][1]
        elif transactions[x][0] == 'WITHDRAW':
            totalbank -= transactions[x][1]

    for x in range(len(transfers)):
        if transfers[x][0] == user:
            totaltransfer += transfers[x][2]
        elif transfers[x][1] == user:
            totaltransfer -= transfers[x][2]

    return int(totalbank + totaltransfer)


# /// Bot Commands ///

@bot.slash_command(description='Display the current balance')
@discord.guild_only()
async def getbalance(ctx):
    with open('./data/balance.txt', 'r') as f:
        balance = f.read()
        balance = float(balance)
    await ctx.respond(f'The current balance is {balance:,} coins!')


@bot.slash_command(description='Displays the last transaction of the profile or user')
async def getlastransaction(
        ctx,
        user: discord.Option(choices=config['coop']['minecraftnames'], required=False)
):
    amount, timestamp, action, initiator = getlasttransaction(user)
    if amount == 0:
        await ctx.respond(f'{user} has no saved transactions')
    else:
        timestamp = '<t:' + str(int(timestamp / 1000)) + ':f>'

        embed = discord.Embed(title='Latest transaction info', color=config['customization']['embedcolor'])

        embed.add_field(name='Amount', value=f'{amount:,}')

        embed.add_field(name='Type', value=action)

        embed.add_field(name='Initiator', value=initiator)

        embed.add_field(name='Timestamp', value=timestamp)

        await ctx.respond(embed=embed)


@bot.slash_command(description='Displays total difference in deposits and withdraws of a given user')
async def getdebt(ctx, user: discord.Option(choices=config['coop']['minecraftnames'])):
    debt = gettotaldebt(user)
    amount, timestamp, action, initiator = getlasttransaction(user)

    embed = discord.Embed(title=f'Total debt of {user}', color=config['customization']['embedcolor'])
    embed.set_thumbnail(url=f'https://mc-heads.net/head/{user}')
    if debt <= 0:
        embed.add_field(name='Amount', value=f'{user} is {-debt:,} coins in debt!')
    else:
        embed.add_field(name='Amount', value=f'{user} has {debt:,} coins to spend!')

    timestamp = '<t:' + str(int(timestamp / 1000)) + ':f>'
    embed.add_field(name='Last Transaction', value='', inline=False)
    if amount == 0:
        embed.add_field(name=f'{user} does not have any saved transactions', value='')
    else:
        embed.add_field(name='Type', value=action)
        embed.add_field(name='Initiator', value=initiator)
        embed.add_field(name='Amount', value=f'{amount:,}')
        embed.add_field(name='Timestamp', value=timestamp)
    await ctx.respond(embed=embed)


@bot.slash_command(description='Transfer debt from another user to you')
async def transferdebt(ctx, removefrom: discord.Option(choices=config['coop']['minecraftnames'] + ['Donate to bank']),
                       amount: discord.Option(str),
                       reason: discord.Option(str)):
    if ctx.user.id not in config['coop']['discordids']:
        await ctx.respond('This command is for coop members only!', ephemeral=True)
    else:
        if removefrom == idtoid[ctx.user.id]:  # Check if user tries to transfer his own debt to himeself
            await ctx.respond('You cannot transfer your own debt to yourself', ephemeral=True)
        else:
            units = {'k': 1000, 'K': 1000, 'm': 1000000, 'M': 1000000, 'b': 1000000000, 'B': 1000000000}
            try:
                amount = float(amount)  # Check if amount is already int
            except ValueError:
                unit = amount[-1]  # Write last char to var for conversion with units dict
                try:
                    amount = float(amount[:-1])  # Check if input is able to converted to float without last char
                except ValueError:
                    await ctx.respond('Formatting error! Check you input.',
                                      ephemeral=True)  # Throw error if input is else
                else:
                    if amount > 1000000000:
                        await ctx.respond(f'Amount too large!', ephemeral=True)
                        try:
                            amount *= units[unit]  # Check if input includes multiple chars
                        except KeyError:
                            await ctx.respond('Formatting error! Check you input.',
                                              ephemeral=True)  # Throw error if input is else
                        else:
                            if amount <= 0:
                                await ctx.respond('Amount cannot be negative/0', ephemeral=True)
                            else:
                                cur.execute(f'''
                                    INSERT INTO transferred (removefrom,addto,amount, reason) 
                                    VALUES('{removefrom}', '{idtoid[ctx.user.id]}', '{amount}', '{reason}')
                                    ''')
                                con.commit()
                                await ctx.respond(
                                    f"Successfully transferred {f'{amount:,}'} coins of {removefrom}'s debt to youself")
            else:
                if amount <= 0:
                    await ctx.respond('Amount cannot be negative / 0', ephemeral=True)
                else:
                    if amount > 1000000000:
                        await ctx.respond(f'Amount too large!', ephemeral=True)
                    else:
                        cur.execute(f'''
                                            INSERT INTO transferred (removefrom,addto,amount, reason) 
                                            VALUES('{removefrom}', '{idtoid[ctx.user.id]}', '{amount}', '{reason}')
                                            ''')
                        con.commit()
                        await ctx.respond(
                            f"Successfully transferred {f'{amount:,}'} coins of {removefrom}'s debt to youself"
                        )


@bot.slash_command(description='Displays information about the last 5 transfers assciated with you account')
async def transferinfo(ctx, user: discord.Option(choices=config['coop']['minecraftnames'])):
    embed = discord.Embed(title=f'Last 5 Transfers of {user}', color=config['customization']['embedcolor'])
    embed.set_thumbnail(url=f'https://mc-heads.net/head/{user}')

    transfers = cur.execute(f'''
    SELECT amount, removefrom, addto, reason 
    FROM transferred 
    WHERE addto == '{user}' OR removefrom == '{user}'
    ORDER BY "index"
    LIMIT 5
    ''')

    transfers = transfers.fetchall()
    for x in range(len(transfers)):
        embed.add_field(name=f'Transfer {x + 1}', value='', inline=False)
        embed.add_field(name='Amount', value=transfers[x][0])
        embed.add_field(name='Removefrom', value=transfers[x][1])
        embed.add_field(name='Addto', value=transfers[x][2])
        embed.add_field(name='Reason', value=transfers[x][3])

    await ctx.respond(embed=embed)


@bot.slash_command(description='Display all information about all users, their debt/credit and the current balance')
async def getall(ctx):
    embed = discord.Embed(title='Debt and Credit Leaderboard', color=config['customization']['embedcolor'])

    posleaderboard = {}
    negleaderboard = {}
    embed.add_field(name='Credit leaderboard', value='')
    for x in range(len(config['coop']['minecraftnames'])):
        userbalance = gettotaldebt(config['coop']['minecraftnames'][x])
        if userbalance >= 0:
            posleaderboard[config['coop']['minecraftnames'][x]] = userbalance
    sortedlist = sorted(posleaderboard.items(), key=lambda e: e[1], reverse=True)
    for x in range(len(sortedlist)):
        embed.add_field(name=f'{x + 1}. Place: {sortedlist[x][0]}',
                        value=f'with {sortedlist[x][1]:,} coins to spend!',
                        inline=False)

    embed.add_field(name='Debt leaderboard', value='', inline=False)
    for x in range(len(config['coop']['minecraftnames'])):
        userbalance = gettotaldebt(config['coop']['minecraftnames'][x])
        if userbalance < 0:
            negleaderboard[config['coop']['minecraftnames'][x]] = userbalance
    sortedlist = sorted(negleaderboard.items(), key=lambda e: e[1])
    for x in range(len(sortedlist)):
        embed.add_field(name=f'{x + 1}. Place: {sortedlist[x][0]}',
                        value=f'being {-sortedlist[x][1]:,} coins in debt!',
                        inline=False)

    with open('./data/balance.txt', 'r') as f:
        balance = f.read()
        balance = float(balance)
    embed.add_field(name='Total balance', value=f'{balance:,} coins', inline=False)
    await ctx.respond(embed=embed)


@bot.slash_command(description='Manually polls the Hypixel API')
@discord.default_permissions(administrator=True)
async def forcereload(ctx: discord.ApplicationContext):
    await bot.hypixel_getdata()
    await ctx.respond('Reload successfull!', ephemeral=True)


bot.run(config['keys']['discord'])
