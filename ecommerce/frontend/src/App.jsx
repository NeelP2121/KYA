import { useState, useEffect } from 'react'

function App() {
  const [products, setProducts] = useState([])
  const [orders, setOrders] = useState([])
  const [cart, setCart] = useState([])
  
  const [searchQuery, setSearchQuery] = useState('')
  const [isOrdersModalOpen, setIsOrdersModalOpen] = useState(false)
  const [isCartModalOpen, setIsCartModalOpen] = useState(false)
  
  const [loading, setLoading] = useState(false)
  const [checkingOut, setCheckingOut] = useState(false)

  useEffect(() => {
    fetchProducts()
    fetchOrders()
  }, [])

  const fetchProducts = async (query = '') => {
    setLoading(true)
    try {
      let url = '/api/products'
      if (query.trim()) {
        url = `/api/products/search?q=${encodeURIComponent(query)}`
      }
      const res = await fetch(url)
      const data = await res.json()
      setProducts(data)
    } catch (err) {
      console.error("Error fetching products", err)
    }
    setLoading(false)
  }

  const fetchOrders = async () => {
    try {
      const res = await fetch('/api/orders')
      const data = await res.json()
      setOrders(data.reverse()) // show newest first
    } catch (err) {
      console.error("Error fetching orders", err)
    }
  }

  const handleSearch = (e) => {
    e.preventDefault()
    fetchProducts(searchQuery)
  }

  const addToCart = (product) => {
    setCart(prev => {
      const existing = prev.find(item => item.id === product.id)
      if (existing) {
        return prev.map(item => item.id === product.id ? { ...item, quantity: item.quantity + 1 } : item)
      }
      return [...prev, { ...product, quantity: 1 }]
    })
  }

  const removeFromCart = (productId) => {
    setCart(prev => prev.filter(item => item.id !== productId))
  }

  const checkout = async () => {
    if (cart.length === 0) return
    setCheckingOut(true)
    try {
      // Place an order for each distinct item in cart
      for (const item of cart) {
        await fetch('/api/orders', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_id: item.id, quantity: item.quantity })
        })
      }
      await fetchOrders()
      setCart([])
      setIsCartModalOpen(false)
      setIsOrdersModalOpen(true)
    } catch (err) {
      console.error("Error during checkout", err)
      alert("Failed to checkout. Please try again.")
    }
    setCheckingOut(false)
  }

  const cancelOrder = async (orderId) => {
    try {
      const res = await fetch(`/api/orders/${orderId}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        await fetchOrders()
      }
    } catch (err) {
      console.error("Error cancelling order", err)
    }
  }

  const cartTotal = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0)

  return (
    <div className="min-h-screen bg-white">
      {/* Navigation */}
      <nav className="sticky top-0 z-50 glass-panel border-b border-gray-100 px-6 py-4 flex justify-between items-center">
        <h1 className="text-2xl font-bold tracking-tighter" onClick={() => {setSearchQuery(''); fetchProducts()}} style={{cursor: 'pointer'}}>
          SOLE<span className="text-gray-400">SPACE</span>.
        </h1>
        
        {/* Prominent Search in Nav for Desktop */}
        <div className="hidden md:block flex-1 max-w-xl mx-8">
           <form onSubmit={handleSearch} className="relative group">
            <input 
              type="text" 
              placeholder="Search sneakers (AI semantic search)..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded-full bg-gray-50 border border-gray-200 py-3 pl-6 pr-12 text-sm outline-none focus:ring-2 focus:ring-black focus:border-black transition-all group-hover:bg-white shadow-sm"
            />
            <button type="submit" className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black transition-colors">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          </form>
        </div>

        <div className="flex gap-4 items-center">
          {/* Cart Button */}
          <button onClick={() => setIsCartModalOpen(true)} className="relative p-2 hover:bg-gray-100 rounded-full transition-colors flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z" />
            </svg>
            <span className="font-semibold text-sm hidden sm:block">Cart</span>
            {cart.length > 0 && (
               <span className="absolute -top-1 -right-1 bg-black text-white text-[10px] h-5 w-5 rounded-full flex items-center justify-center font-bold border-2 border-white">
                 {cart.length}
               </span>
            )}
          </button>
          
          {/* Orders Button */}
          <button onClick={() => setIsOrdersModalOpen(true)} className="relative p-2 hover:bg-gray-100 rounded-full transition-colors flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span className="font-semibold text-sm hidden sm:block">Orders</span>
          </button>
        </div>
      </nav>

      {/* Hero Section */}
      <header className="relative pt-20 pb-16 px-6 max-w-7xl mx-auto text-center">
        <h2 className="text-6xl md:text-8xl font-black tracking-tight mb-6 hover:scale-105 transition-transform duration-500">
          YOUR <span className="text-transparent bg-clip-text bg-gradient-to-r from-gray-900 to-gray-400">GRAILS.</span>
        </h2>
        <p className="text-xl text-gray-500 max-w-2xl mx-auto mb-10">
          Discover the most sought-after sneakers on the planet. Powered by natural language vector search.
        </p>
        
        {/* Mobile Search */}
        <div className="md:hidden w-full max-w-md mx-auto mb-10">
           <form onSubmit={handleSearch} className="relative">
            <input 
              type="text" 
              placeholder="Search sneakers..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded-full border border-gray-200 py-3 pl-6 pr-12 text-sm outline-none focus:ring-2 focus:ring-black transition-all shadow-sm"
            />
            <button type="submit" className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          </form>
        </div>
      </header>

      {/* Product Grid */}
      <main className="max-w-7xl mx-auto px-6 pb-24">
        {loading ? (
          <div className="flex justify-center items-center h-64">
             <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-black"></div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-8">
            {products.map(product => (
              <div key={product.id} className="product-card flex flex-col border border-gray-100 group">
                <div className="relative h-72 overflow-hidden bg-gray-50 mb-4 p-4">
                  <div className="absolute inset-0 bg-gray-100 z-0"></div>
                  <img src={product.image_url} alt={product.name} className="product-image relative z-10 w-full h-full object-contain filter drop-shadow-xl" loading="lazy" />
                  <div className="absolute top-4 left-4 z-20 bg-white/90 backdrop-blur-md px-3 py-1.5 text-xs font-bold rounded-full uppercase tracking-widest shadow-sm">
                    {product.brand}
                  </div>
                </div>
                <div className="flex-grow flex flex-col justify-between px-5 pb-5">
                  <div>
                    <h3 className="font-bold text-lg leading-tight mb-2 group-hover:text-gray-700 transition-colors">{product.name}</h3>
                    <p className="text-gray-500 text-sm line-clamp-2 mb-5 leading-relaxed">{product.description}</p>
                  </div>
                  <div className="flex justify-between items-center mt-auto border-t border-gray-100 pt-4">
                    <span className="font-bold text-xl">${product.price.toFixed(2)}</span>
                    <button 
                      onClick={() => addToCart(product)}
                      className="bg-black text-white px-5 py-2.5 rounded-full text-sm font-medium hover:bg-gray-800 transition-colors shadow-md hover:shadow-lg flex gap-2 items-center justify-center w-full mt-2"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" />
                      </svg>
                      Add to cart
                    </button>
                  </div>
                </div>
              </div>
            ))}
            {products.length === 0 && (
              <div className="col-span-full flex flex-col items-center py-20 text-gray-400">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-16 w-16 mb-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-xl font-medium">No sneakers found matching your vibe.</p>
                <button onClick={() => {setSearchQuery(''); fetchProducts()}} className="mt-4 border border-gray-300 px-6 py-2 rounded-full text-sm hover:bg-gray-50 text-black">Clear Search</button>
              </div>
            )}
          </div>
        )}
      </main>

      {/* Cart Modal */}
      {isCartModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-end bg-black/40 backdrop-blur-sm transition-opacity">
          <div className="bg-white w-full max-w-md h-full overflow-hidden flex flex-col shadow-2xl relative animate-slide-in">
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50">
              <h3 className="text-2xl font-black tracking-tight">Your Cart</h3>
              <button onClick={() => setIsCartModalOpen(false)} className="bg-white hover:bg-gray-100 p-2 rounded-full transition-colors shadow-sm">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
            </div>
            
            <div className="overflow-y-auto flex-1 p-6 flex flex-col gap-5">
              {cart.length === 0 ? (
                <div className="flex-1 flex flex-col items-center justify-center text-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-16 w-16 text-gray-200 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z" />
                  </svg>
                   <p className="text-gray-500 font-medium">Your cart is empty.</p>
                   <button onClick={() => setIsCartModalOpen(false)} className="mt-4 underline text-sm text-gray-400">Continue Shopping</button>
                </div>
              ) : (
                cart.map(item => (
                  <div key={item.id} className="flex gap-4 p-3 rounded-2xl border border-gray-100 bg-white shadow-sm relative group">
                    <button 
                       onClick={() => removeFromCart(item.id)}
                       className="absolute -top-2 -right-2 bg-white border border-gray-200 text-black w-6 h-6 flex items-center justify-center rounded-full opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-50 hover:text-red-500 hover:border-red-200"
                    >×</button>
                    <div className="w-24 h-24 bg-gray-50 rounded-xl overflow-hidden flex-shrink-0 relative">
                       <img src={item.image_url} className="w-full h-full object-contain p-2 absolute inset-0 m-auto mix-blend-multiply" />
                    </div>
                    <div className="flex-1 flex flex-col justify-center">
                      <p className="font-bold text-sm line-clamp-2 leading-tight pr-4">{item.name}</p>
                      <p className="text-gray-400 text-xs mt-1 uppercase tracking-wider font-semibold">{item.brand}</p>
                      <div className="mt-3 flex justify-between items-end">
                        <span className="text-sm font-bold">${item.price.toFixed(2)}</span>
                        <span className="text-xs bg-gray-100 px-2 py-1 rounded-md font-medium text-gray-600">Qty: {item.quantity}</span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
            
            {cart.length > 0 && (
              <div className="p-6 bg-gray-50 border-t border-gray-100">
                <div className="flex justify-between items-center mb-6">
                  <span className="text-gray-500 font-medium">Subtotal</span>
                  <span className="text-2xl font-black tracking-tight">${cartTotal.toFixed(2)}</span>
                </div>
                <button 
                  onClick={checkout}
                  disabled={checkingOut}
                  className="w-full bg-black text-white py-4 rounded-xl font-bold text-lg hover:bg-gray-800 transition-colors shadow-lg disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                  {checkingOut ? (
                    <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div> Processing...</>
                  ) : (
                    'Secure Checkout'
                  )}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Orders Modal */}
      {isOrdersModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm transition-opacity">
          <div className="bg-white rounded-3xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col shadow-2xl relative">
            <div className="p-6 md:p-8 border-b border-gray-100 flex justify-between items-center bg-gray-50">
              <div>
                <h3 className="text-2xl font-black tracking-tight mb-1">Order History</h3>
                <p className="text-sm text-gray-500">Track and manage your recent purchases.</p>
              </div>
              <button onClick={() => setIsOrdersModalOpen(false)} className="bg-white shadow-sm hover:shadow p-2 rounded-full transition-all border border-gray-100">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
            </div>
            
            <div className="overflow-y-auto p-6 md:p-8 flex flex-col gap-6 bg-white">
              {orders.length === 0 ? (
                <div className="text-center flex flex-col items-center justify-center py-16">
                  <div className="w-20 h-20 bg-gray-50 rounded-full flex items-center justify-center mb-4">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </div>
                  <p className="text-gray-900 font-semibold mb-1">No orders yet</p>
                  <p className="text-gray-500 text-sm">When you place an order, it will appear here.</p>
                </div>
              ) : (
                orders.map(order => (
                  <div key={order.id} className={`flex flex-col sm:flex-row gap-5 p-5 rounded-2xl border ${order.status === 'cancelled' ? 'border-gray-100 bg-gray-50 opacity-75' : 'border-gray-200 bg-white hover:border-gray-300'} transition-colors`}>
                    <div className="w-full sm:w-28 h-28 bg-gray-100/50 rounded-xl overflow-hidden flex-shrink-0 relative">
                      <img src={order.product?.image_url} className="w-full h-full object-contain p-2 mix-blend-multiply" />
                    </div>
                    <div className="flex-1 flex flex-col">
                      <div className="flex justify-between items-start gap-4">
                        <div>
                          <p className="font-bold text-lg leading-tight mb-1">{order.product?.name}</p>
                          <p className="text-gray-400 text-xs font-semibold tracking-wider uppercase">{order.product?.brand}</p>
                        </div>
                        <span className="font-black text-lg whitespace-nowrap">${(order.product?.price * order.quantity).toFixed(2)}</span>
                      </div>
                      
                      <div className="mt-auto pt-4 flex flex-wrap justify-between items-center gap-3 w-full">
                        <div className="flex gap-4 text-xs font-medium text-gray-500">
                           <span className="bg-gray-100 px-3 py-1.5 rounded-md">Order #{order.id}</span>
                           <span className="bg-gray-100 px-3 py-1.5 rounded-md">Qty: {order.quantity}</span>
                        </div>
                        <div className="flex items-center gap-4">
                          <span className={`text-xs font-black tracking-wider uppercase px-3 py-1.5 rounded-md ${
                            order.status === 'cancelled' ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-600'
                          }`}>
                            {order.status}
                          </span>
                          {order.status !== 'cancelled' && (
                            <button 
                              onClick={() => cancelOrder(order.id)}
                              className="text-xs font-semibold text-gray-400 hover:text-red-500 transition-colors uppercase tracking-wider"
                            >
                              Cancel Order
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
