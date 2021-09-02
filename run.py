#!/usr/bin/env python3
from tsuu import create_app

app = create_app('config')
app.run(host='0.0.0.0', port=5500, debug=True)
