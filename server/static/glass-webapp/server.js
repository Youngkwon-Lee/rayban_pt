var http = require('http');
var fs = require('fs');
var path = require('path');

var PORT = process.env.PORT || 3000;

var mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

var server = http.createServer(function (req, res) {
  var requestPath = req.url.split('?')[0];
  var isConnectPath = requestPath.indexOf('/connect/') === 0;
  var filePath = '.' + (requestPath === '/' || isConnectPath ? '/index.html' : requestPath);
  var resolved = path.resolve(filePath);
  var root = path.resolve('.');

  if (resolved.indexOf(root) !== 0) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Forbidden');
    return;
  }

  fs.readFile(resolved, function (err, content) {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not Found');
      return;
    }
    var contentType = mimeTypes[path.extname(resolved)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(content);
  });
});

server.listen(PORT, function () {
  console.log('MRBD HUD server running at http://localhost:' + PORT);
});
