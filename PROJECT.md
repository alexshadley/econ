# Econ Game

we're going to make a terminal application that's a econ simulator. we will
have 3 llm agents each trying to make as much money as possible.

## Toy economy

Agents will work within a toy economy.

There are four commodities:
- ore
- metal
- parts
- cars

each commodity comes in integer units

And there are three types of factory:
- metal factory
- part factory
- car factory

each factory comes in integer units. a factory turns one unit of the previous
commodity into one of the next. it takes 1 minute. it costs $2 in the process.
if a firm owns multiple of a type of factory, operating costs go down according
to some logarithm, where $1 is the minimum cost (at infinite factory units).

Firms can buy and sell commodities to each other.

Ore can be bought from the game for $1, and cars can be sold for $10 to the
game. Other commodities can not be bought or sold to the game.

Factory units can be bought from the game for $10 each.

## Starting state

Each agent starts with a firm that only has one type of factory (one for metal,
one for parts, one for cars). 10 units of factory. $100 in the bank.

## Implementation

App will be a python script

make sure to use uv for everything. so
- `uv run python` to run code
- `uv sync` to install deps
- so on and so forth

Use async to manage multiple llm-based agents running at the same time. Game
will be real-time. Most actions are instant. But running factories takes 1 min,
as I mentioned, during which the factory is busy and can't be used for another
input

## Agents

Agents will have tools for the following tasks:
- sending messages to other agents
  - messages should have threads
- sending contracts to other agents
  - contract is an offer to buy/sell a quantity of commodities for a certain price
- accepting contracts
- view unread messages and contract offers
- starting factories
- buying and selling commodities to the game

Agents will all run continuously and try to make money. Stop the game after 5 minutes.

use gpt 5 mini. you can use the openai sdk





