from aiohttp import web
from . import bot

import logging


INDEX_HTML = open('index.html', 'rb').read()


async def handle_index(request):
    return web.Response(content_type='text/html', body=INDEX_HTML)

async def handle_start_request(request):
    params = await request.post()
    if 'url' not in params:
        return web.Response(status=400, text='Invalid request.')
    url = params['url']

    server, port, password = url.split('#')[1].split('...')
    bot.spawn(server, port, password)

    return web.Response(text=f'Bots are on their way!')


app = web.Application()
app.add_routes([
    web.get('/', handle_index),
    web.post('/start', handle_start_request),
])

if __name__ == '__main__':
    logging.basicConfig()
    logging.getLogger().setLevel(logging.INFO)
    web.run_app(app, port=6565)
