function handler(event) {
    var request = event.request;
    var uri = request.uri;

    // Check if the URI has a file extension (e.g. .js, .css, .svg, .png, .ico, .json)
    if (uri.indexOf('.') !== -1) {
        return request;
    }

    // Rewrite all SPA client-side routes to /index.html
    request.uri = '/index.html';
    return request;
}
